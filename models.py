#!/usr/bin/env python
# -*- coding: utf-8 -*-

from bs4 import BeautifulSoup
import requests

from datetime import datetime as dt
from urllib.parse import unquote
from urllib.parse import urljoin
from urllib.parse import urlparse
import http.cookiejar
import json
import mimetypes
import os
import re
import sys
import time
import traceback


FANTIA_URL_RE = re.compile(r"(?:https?://(?:(?:www\.)?(?:fantia\.jp/(fanclubs|posts)/)))([0-9]+)")
EXTERNAL_LINKS_RE = re.compile(r"(?:[\s]+)?((?:(?:https?://)?(?:(?:www\.)?(?:mega\.nz|mediafire\.com|(?:drive|docs)\.google\.com|youtube.com|dropbox.com)\/))[^\s]+)")

DOMAIN = "fantia.jp"
BASE_URL = "https://fantia.jp/"

LOGIN_SIGNIN_URL = "https://fantia.jp/sessions/signin"
LOGIN_SESSION_URL = "https://fantia.jp/sessions"

ME_API = "https://fantia.jp/api/v1/me"

FANCLUB_API = "https://fantia.jp/api/v1/fanclubs/{}"
FANCLUBS_FOLLOWING_API = "https://fantia.jp/api/v1/me/fanclubs"
FANCLUBS_PAID_HTML = "https://fantia.jp/mypage/users/plans?type=not_free"
FANCLUB_POSTS_HTML = "https://fantia.jp/fanclubs/{}/posts?page={}"

POST_API = "https://fantia.jp/api/v1/posts/{}"
POST_URL = "https://fantia.jp/posts"
POST_RELATIVE_URL = "/posts/"
RENEW_STR = "更新"

CRAWLJOB_FILENAME = "external_links.crawljob"

MIMETYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm"
}


class FantiaClub:
    def __init__(self, fanclub_id):
        self.id = fanclub_id


class FantiaDownloaderCLI:
    def __init__(self, session_arg, chunk_size=1024 * 1024 * 5, dump_metadata=False, parse_for_external_links=False, download_thumb=False, directory=None, quiet=True, continue_on_error=False, use_server_filenames=False, mark_incomplete_posts=False, month_limit=None, exclude_file=None):
        # self.email = email
        # self.password = password
        self.session_arg = session_arg
        self.chunk_size = chunk_size
        self.dump_metadata = dump_metadata
        self.parse_for_external_links = parse_for_external_links
        self.download_thumb = download_thumb
        self.directory = directory or ""
        self.quiet = quiet
        self.continue_on_error = continue_on_error
        self.use_server_filenames = use_server_filenames
        self.mark_incomplete_posts = mark_incomplete_posts
        self.month_limit = dt.strptime(month_limit, "%Y-%m") if month_limit else None
        self.exclude_file = exclude_file
        self.exclusions = []
        self.session = requests.session()
        self.login()
        self.create_exclusions()

    def output(self, output):
        """Write output to the console."""
        if not self.quiet:
            try:
                sys.stdout.write(output.encode(sys.stdout.encoding, errors="backslashreplace").decode(sys.stdout.encoding))
                sys.stdout.flush()
            except (UnicodeEncodeError, UnicodeDecodeError):
                sys.stdout.buffer.write(output.encode("utf-8"))
                sys.stdout.flush()

    

        # Login flow, requires reCAPTCHA token

        # login_json = {
        #     "utf8": "✓",
        #     "button": "",
        #     "user[email]": self.email,
        #     "user[password]": self.password,
        # }

        # login_session = self.session.get(LOGIN_SIGNIN_URL)
        # login_page = BeautifulSoup(login_session.text, "html.parser")
        # authenticity_token = login_page.select_one("input[name=\"authenticity_token\"]")["value"]
        # print(login_page.select_one("input[name=\"recaptcha_response\"]"))
        # login_json["authenticity_token"] = authenticity_token
        # login_json["recaptcha_response"] = ...

        # create_session = self.session.post(LOGIN_SESSION_URL, data=login_json)
        # if not create_session.headers.get("Location"):
        #     sys.exit("Error: Bad login form data")
        # elif create_session.headers["Location"] == LOGIN_SIGNIN_URL:
        #     sys.exit("Error: Failed to login. Please verify your username and password")

        # check_user = self.session.get(ME_API)
        # if not (check_user.ok or check_user.status_code == 304):
        #     sys.exit("Error: Invalid session")

    def create_exclusions(self):
        """Read files to exclude from downloading."""
        if self.exclude_file:
            with open(self.exclude_file, "r") as file:
                self.exclusions = [line.rstrip("\n") for line in file]

    def process_content_type(self, url):
        """Process the Content-Type from a request header and use it to build a filename."""
        url_header = self.session.head(url, allow_redirects=True)
        mimetype = url_header.headers["Content-Type"]
        extension = guess_extension(mimetype, url)
        return extension

    def collect_post_titles(self, post_metadata):
        """
        Collect all post titles to check for duplicate names and rename as necessary by appending a counter.
        """
        post_titles = []
        for post in post_metadata["post_contents"]:
            try:
                potential_title = post["title"] or post["parent_post"]["title"]
                if not potential_title:
                    potential_title = str(post["id"])
            except KeyError:
                potential_title = str(post["id"])

            title = potential_title
            counter = 2
            while title in post_titles:
                title = potential_title + "_{}".format(counter)
                counter += 1
            post_titles.append(title)

        return post_titles

    def download_fanclub_metadata(self, fanclub):
        """Download fanclub header, icon, and custom background."""
        response = self.session.get(FANCLUB_API.format(fanclub.id))
        response.raise_for_status()
        fanclub_json = json.loads(response.text)

        fanclub_creator = fanclub_json["fanclub"]["creator_name"]
        fanclub_directory = os.path.join(self.directory, sanitize_for_path(fanclub_creator))
        os.makedirs(fanclub_directory, exist_ok=True)

        self.save_metadata(fanclub_json, fanclub_directory)

        header_url = fanclub_json["fanclub"]["cover"]["original"]
        if header_url:
            header_filename = os.path.join(fanclub_directory, "header" + self.process_content_type(header_url))
            self.output("Downloading fanclub header...\n")
            self.perform_download(header_url, header_filename, use_server_filename=self.use_server_filenames)

        fanclub_icon_url = fanclub_json["fanclub"]["icon"]["original"]
        if fanclub_icon_url:
            fanclub_icon_filename = os.path.join(fanclub_directory, "icon" + self.process_content_type(fanclub_icon_url))
            self.output("Downloading fanclub icon...\n")
            self.perform_download(fanclub_icon_url, fanclub_icon_filename, use_server_filename=self.use_server_filenames)

        background_url = fanclub_json["fanclub"]["background"]
        if background_url:
            background_filename = os.path.join(fanclub_directory, "background" + self.process_content_type(background_url))
            self.output("Downloading fanclub background...\n")
            self.perform_download(background_url, background_filename, use_server_filename=self.use_server_filenames)

    

    def download_photo(self, photo_url, photo_counter, gallery_directory):
        """Download a photo to the post's directory."""
        extension = self.process_content_type(photo_url)
        filename = os.path.join(gallery_directory, str(photo_counter) + extension) if gallery_directory else str()
        self.perform_download(photo_url, filename, use_server_filename=self.use_server_filenames)

    def download_file(self, download_url, filename, post_directory):
        """Download a file to the post's directory."""
        self.perform_download(download_url, filename, use_server_filename=True) # Force serve filenames to prevent duplicate collision

   

class FantiaDownloader:
    def __init__(self, session_key):
        self.session_key = session_key
        self.session = requests.session()
        self.login()

    def login(self):
        cookie = requests.cookies.create_cookie(domain=DOMAIN, name='_session_id', value=self.session_key)
        self.session.cookies.set_cookie(cookie)

        check_user = self.session.get(ME_API)
        if not (check_user.ok or check_user.status_code == 304):
            raise Exception('Unable to authenticate user')

    def get_fanclub_metadata(self, fanclub):
        """Download fanclub header, icon, and custom background."""
        response = self.session.get(FANCLUB_API.format(fanclub.id), proxies = self.proxy)
        response.raise_for_status()
        fanclub_json = json.loads(response.text)

        fanclub = {}
        fanclub['creator'] = fanclub_json['fanclub']['creator_name']

        header_url = get_multi_level_value(fanclub_json, 'fanclub', 'cover', 'original')
        if header_url is not None:
            header_filename = os.path.join(fanclub_directory, "header" + self.process_content_type(header_url))
            fanclub['header'] = {}
            fanclub['header']['bytes'] = fetch_file_and_data()
            fanclub['header']['content-type'] = None

        icon_url = get_multi_level_value(fanclub_json, 'fanclub', 'icon', 'original')
        if icon_url is not None:
            fanclub['icon'] = {}
            fanclub['icon']['bytes'] = fetch_file_and_data()
            fanclub['icon']['content-type'] = None

        background_url = get_multi_level_value(fanclub_json, 'fanclub', 'background')
        if background_url is not None:
            fanclub['background'] = {}
            fanclub['background']['bytes'] = fetch_file_and_data()
            fanclub['background']['content-type'] = None

    def get_post(self, post_id):
        post = {}

        response = self.session.get(POST_API.format(post_id))
        response.raise_for_status()
        post_json = json.loads(response.text)["post"]

        post['id'] = str(post_json['id'])
        post['creator'] = post_json['fanclub']['creator_name']
        post['title'] = get_value(post_json, 'title')
        post['content'] = get_value(post_json, 'post_contents')
        post['thumbnail_url'] = get_multi_level_value(post_json, 'thumb', 'original')

        post_titles = self.collect_post_titles(post_json)

        for post_index, post in enumerate(post_contents):
            post_title = post_titles[post_index]
            self.download_post_content(post, post_directory, post_title)

    def get_fanclub_posts(self, fanclub, page_number = 1):
        post_ids = []

        response = self.session.get(FANCLUB_POSTS_HTML.format(fanclub.id, page_number))
        response.raise_for_status()
        response_page = BeautifulSoup(response.text, 'html.parser')
        posts = response_page.select('div.post')

        parsed_posts = []
        for post in posts:
            link = post.select_one('a.link-block')['href']
            post_id = link.lstrip(POST_RELATIVE_URL)
            date_string = post.select_one('span.post-date').text.rstrip(RENEW_STR)
            parsed_posts.append({
                'id': post_id,
                'created_at': date_string
            })

        for post in parsed_posts:


     def parse_post(self, post_json, post_directory, post_title):
        """Parse the post's content to determine whether to save the content as a photo gallery or file."""
        if post_json.get("visible_status") == "visible":
            if post_json.get("category") == "photo_gallery":
                photo_gallery = post_json["post_content_photos"]
                photo_counter = 0
                gallery_directory = os.path.join(post_directory, sanitize_for_path(post_title))
                os.makedirs(gallery_directory, exist_ok=True)
                for photo in photo_gallery:
                    photo_url = photo["url"]["original"]
                    self.download_photo(photo_url, photo_counter, gallery_directory)
                    photo_counter += 1
            elif post_json.get("category") == "file":
                filename = os.path.join(post_directory, post_json["filename"])
                download_url = urljoin(POST_URL, post_json["download_uri"])
                self.download_file(download_url, filename, post_directory)
            elif post_json.get("category") == "embed":
                if self.parse_for_external_links:
                    # TODO: Check what URLs are allowed as embeds
                    link_as_list = [post_json["embed_url"]]
                    self.output("Adding embedded link {0} to {1}.\n".format(post_json["embed_url"], CRAWLJOB_FILENAME))
                    build_crawljob(link_as_list, self.directory, post_directory)
            elif post_json.get("category") == "blog":
                blog_comment = post_json["comment"]
                blog_json = json.loads(blog_comment)
                photo_counter = 0
                gallery_directory = os.path.join(post_directory, sanitize_for_path(post_title))
                os.makedirs(gallery_directory, exist_ok=True)
                for op in blog_json["ops"]:
                    if type(op["insert"]) is dict and op["insert"].get("fantiaImage"):
                        photo_url = urljoin(BASE_URL, op["insert"]["fantiaImage"]["original_url"])
                        self.download_photo(photo_url, photo_counter, gallery_directory)
                        photo_counter += 1
            else:
                self.output("Post content category \"{}\" is not supported. Skipping...\n".format(post_json.get("category")))

            if self.parse_for_external_links:
                post_description = post_json["comment"] or ""
                self.parse_external_links(post_description, os.path.abspath(post_directory))
        else:
            self.output("Post content not available on current plan. Skipping...\n")



    def get_fanclub(self, fanclub):
        """Download a fanclub."""
        self.output("Downloading fanclub {}...\n".format(fanclub.id))
        post_ids = self.fetch_fanclub_posts(fanclub)

        if self.dump_metadata:
            self.download_fanclub_metadata(fanclub)

        for post_id in post_ids:
            try:
                self.download_post(post_id)
            except KeyboardInterrupt:
                raise
            except:
                if self.continue_on_error:
                    self.output("Encountered an error downloading post. Skipping...\n")
                    traceback.print_exc()
                    continue
                else:
                    raise

    def get_paid_fanclubs(self):
        """Download all fanclubs backed on a paid plan."""
        response = self.session.get(FANCLUBS_PAID_HTML)
        response.raise_for_status()
        response_page = BeautifulSoup(response.text, 'html.parser')
        fanclub_links = response_page.select('div.mb-5-children > div:nth-of-type(1) a[href^="/fanclubs"]')

        for fanclub_link in fanclub_links:
            try:
                fanclub_id = fanclub_link['href'].lstrip('/fanclubs/')
                fanclub = FantiaClub(fanclub_id)
                self.download_fanclub(fanclub)
            except KeyboardInterrupt:
                raise
            except:
                if self.continue_on_error:
                    self.output("Encountered an error downloading fanclub. Skipping...\n")
                    traceback.print_exc()
                    continue
                else:
                    raise

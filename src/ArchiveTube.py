import logging
import pprint
import re
import os
import json
import time
import datetime
import threading
from numbers import Number

from gevent import monkey
from mutagen.mp4 import MP4
import concurrent.futures
from flask import Flask, render_template
from flask_socketio import SocketIO
import yt_dlp
from plexapi.server import PlexServer
import requests
import tempfile

monkey.patch_all()

PERMANENT_RETENTION = -1
VIDEO_EXTENSIONS = {".mp4", ".mkv"}
AUDIO_EXTENSIONS = {".m4a"}
MEDIA_FILE_EXTENSIONS = VIDEO_EXTENSIONS.union(AUDIO_EXTENSIONS)


def video_duration_filter( info, *, incomplete ):
    """
    Match & download only videos that are longer than specified duration. Also matches videos with unknown duration
    """
    duration = info.get('duration')
    if duration and duration < 90:
        return 'The video is too short'
    return None


def number_si_suffix( num: Number ) -> str:
    """
    Convert number to a form with SI suffix

    Shamelessly borrowed from https://stackoverflow.com/a/15485265 - thanks Pyrocater
    """

    for unit in ("", "k", "M", "G", "T", "P", "E", "Z"):
        if abs(num) < 1000.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}Y"


class FancyFormatter(logging.Formatter):
    FMT_SEQ = "\x1b["
    ASCII_COLORS = {
        logging.CRITICAL: "1;31",  # red + bold
        logging.ERROR:    "0;31",  # red
        logging.WARNING:  "0;33",  # yellow
        logging.INFO:     "0;37",  # white
        logging.DEBUG:    "2;37",  # gray
    }

    def __init__( self, fmt ):
        logging.Formatter.__init__(self, fmt)
        self._fmt = fmt

    def format( self, r ):
        return logging.Formatter(self.FMT_SEQ + self.ASCII_COLORS[r.levelno] + "m"
                                 + self._fmt
                                 + self.FMT_SEQ + "0m", "%H:%M:%S"
                                 ).format(r)


class DataHandler:
    def __init__(self):
        #logging.basicConfig(level=logging.INFO, format="%(message)s")
        self.log = logging.getLogger()
        self.log.setLevel(logging.INFO)
        log_shandler = logging.StreamHandler()
        logger_fmt = "%(asctime)s.%(msecs)03d [%(levelname).1s]  %(message)s"
        log_shandler.setFormatter(FancyFormatter(logger_fmt))
        self.log.addHandler(log_shandler)

        self.task_thread = None
        self.task_thread_started = False

        app_name_text = os.path.basename(__file__).replace(".py", "")
        release_version = os.environ.get("RELEASE_VERSION", "unknown")
        self.log.warning(f"{'*' * 50}")
        self.log.warning(f"{app_name_text} Version: {release_version}")
        self.log.warning(f"{'*' * 50}")

        self.download_progress_report_perc = 0
        self.config_folder = "config"
        # TODO: Fix process_channel_errors by making it thread-safe!
        self.process_channel_errors = 0
        self.download_folder = "downloads"
        self.audio_download_folder = "audio_downloads"
        self.media_server_addresses = ""
        self.media_server_tokens = ""
        self.media_server_library_name = "YouTube"
        self.media_server_scan_req_flag = False
        self.video_format_id = os.environ.get("video_format_id", "137")
        self.audio_format_id = os.environ.get("audio_format_id", "140")
        self.defer_hours = float(os.environ.get("defer_hours", "0"))
        self.thread_limit = int(os.environ.get("thread_limit", "1"))
        self.fallback_vcodec = os.environ.get("fallback_vcodec", "vp9")
        self.fallback_acodec = os.environ.get("fallback_acodec", "mp4a")
        self.subtitles = os.environ.get("subtitles", "none").lower()
        self.subtitles = "none" if self.subtitles not in ("none", "embed", "external") else self.subtitles
        self.subtitle_languages = os.environ.get("subtitle_languages", "en").split(",")
        self.include_id_in_filename = True
        self.verbose_logs = os.environ.get("verbose_logs", "false").lower() == "true"
        self.ignore_ssl_errors = False
        self.youtube_slow = False

        # TODO: Add option to control verbose logs from the UI
        if self.verbose_logs:
            self.log.setLevel(logging.DEBUG)
            self.log.debug("Verbose logs enabled")

        self.ytd_extra_parameters = {
            "ffmpeg_location":          "/usr/bin/ffmpeg",
            "verbose":                  self.verbose_logs,
            "logger":                   self.log,
            "quiet":                    True,
            'fragment_retries':         10,
            'retries':                  10,
        }

        # This gets used when self.youtube_slow is True
        self.ytd_slow_parameters = {
            'max_sleep_interval':       20.0,
            'sleep_interval':           10.0,
            'sleep_interval_requests':  0.75,
            'sleep_interval_subtitles': 5
        }

        os.makedirs(self.config_folder, exist_ok=True)
        os.makedirs(self.download_folder, exist_ok=True)
        os.makedirs(self.audio_download_folder, exist_ok=True)

        self.sync_start_times = []
        self.settings_config_file = os.path.join(self.config_folder, "settings_config.json")

        self.req_channel_list = []
        self.channel_list_config_file = os.path.join(self.config_folder, "channel_list.json")

        if os.path.exists(self.settings_config_file):
            self.load_settings_from_file()

        if os.path.exists(self.channel_list_config_file):
            self.load_channel_list_from_file()

        # TODO: Add support for getting cookies directly from the browser (by passing profile directory). It is a PITA
        #       passing cookies.txt manually. Especially since it changes whenever I click on something on YT.
        # TODO: Test if it be sufficient to log in in different browser - that way I can still keep watching YT without
        #       screwing my cookies all the time.
        full_cookies_path = os.path.join(self.config_folder, "cookies.txt")
        if os.path.exists(full_cookies_path):
            self.log.warning("Using provided cookies.txt!")
            self.ytd_extra_parameters["cookiefile"] = full_cookies_path

        if self.ignore_ssl_errors:
            self.ytd_extra_parameters["nocheckcertificate"] = True

        task_thread = threading.Thread(target=self.schedule_checker, daemon=True)
        task_thread.start()

    def load_settings_from_file(self):
        try:
            with open(self.settings_config_file, "r") as json_file:
                ret = json.load(json_file)
                self.sync_start_times = ret.get("sync_start_times", "")
                self.media_server_addresses = ret.get("media_server_addresses", "")
                self.media_server_tokens = ret.get("media_server_tokens", "")
                self.media_server_library_name = ret.get("media_server_library_name", "")
                self.ignore_ssl_errors = ret.get("ignore_ssl_errors", False)
                self.youtube_slow = ret.get("youtube_slow", False)

        except Exception as e:
            self.log.error(f"Error Loading Config: {str(e)}")

        else:
            self.log.info("Settings successfully loaded!")

    def save_settings_to_file_and_reload( self ):
        try:
            with open(self.settings_config_file, "w") as json_file:
                json.dump(
                    {
                        "sync_start_times": self.sync_start_times,
                        "media_server_addresses": self.media_server_addresses,
                        "media_server_tokens": self.media_server_tokens,
                        "media_server_library_name": self.media_server_library_name,
                        "ignore_ssl_errors": self.ignore_ssl_errors,
                        "youtube_slow": self.youtube_slow
                    },
                    json_file,
                    indent=4,
                )

        except Exception as e:
            self.log.error(f"Error Saving Config: {str(e)}")

        else:
            self.log.warning(f"Settings saved, re-applying...")
            self.load_settings_from_file()

    def load_channel_list_from_file(self):
        try:
            with open(self.channel_list_config_file, "r") as json_file:
                channels = json.load(json_file)
            sorted_channels = sorted(channels, key=lambda c: c.get("Name", "").lower())

            self.log.info(f"load_channel_list_from_file> Loading {len(sorted_channels)} channels from {self.channel_list_config_file}")
            for idx, channel in enumerate(sorted_channels):
                try:

                    synced_state = channel.get("Last_Synced", "Never")
                    synced_state = "Incomplete" if synced_state in ["In Progress", "Failed", "Queued"] else synced_state
                    import_Search_Limit = channel.get("Search_Limit", 0)
                    if import_Search_Limit == "":
                        import_Search_Limit = 0

                    full_channel_data = {
                        # ID of channel (immutable)
                        "Id": idx,
                        "Name": channel.get("Name", ""),
                        "Link": channel.get("Link", ""),
                        # Synchronization disabled (paused) or not
                        "Paused": bool(channel.get("Paused", False)),
                        "DL_Days": int(channel.get("DL_Days", 0)),
                        "Keep_Days": int(channel.get("Keep_Days", 0)),
                        "Last_Synced": synced_state,
                        "Item_Count": int(channel.get("Item_Count", 0)),
                        "Item_Size": 0,
                        # Remote media count
                        "Remote_Count": int(channel.get("Remote_Count", 0)),
                        "Filter_Title_Text": channel.get("Filter_Title_Text", ""),
                        "Negate_Filter": bool(channel.get("Negate_Filter", False)),
                        "Search_Limit": int(import_Search_Limit),
                        "Live_Rule": channel.get("Live_Rule", "Ignore"),
                        "Audio_Only": bool(channel.get("Audio_Only", False)),
                        "Use_SponsorBlock": bool(channel.get("Use_SponsorBlock", True)),
                        "Use_Best_Quality": bool(channel.get("Use_Best_Quality", False)),
                        "Write_Info_Json": bool(channel.get("Write_Info_Json", True)),
                        "Set_Mtime": bool(channel.get("Set_Mtime", True)),
                    }

                    itm_count, itm_size = self.count_media_files_for_channel( full_channel_data )
                    full_channel_data["Item_Count"] = itm_count
                    full_channel_data["Item_Size"] = itm_size
                    if full_channel_data["Item_Count"] == -1:
                        full_channel_data["Last_Synced"] = "Never"
                        full_channel_data["Item_Count"] = 0

                    self.req_channel_list.append(full_channel_data)
                    self.log.info(f"load_channel_list_from_file> Channel '{full_channel_data["Name"]}' loaded")
                except ValueError as e:
                    self.log.error(f"load_channel_list_from_file> Failed to load channel ID={idx}: {str(e)}")

        except Exception as e:
            self.log.error(f"load_channel_list_from_file> Error Loading Channels: {str(e)}")
            self.log.exception(e)

    def save_channel_list_to_file(self):
        try:
            with open(self.channel_list_config_file, "w") as json_file:
                json.dump(self.req_channel_list, json_file, indent=4)

        except Exception as e:
            self.log.error(f"Error Saving Channels: {str(e)}")

    def schedule_checker(self):
        self.log.info("Starting periodic checks every 10 minutes to monitor sync start times.")
        self.log.info(f"Current scheduled hours to start sync (in 24-hour format): {self.sync_start_times}")
        while True:
            current_time = datetime.datetime.now()
            within_sync_window = current_time.hour in self.sync_start_times

            if within_sync_window:
                self.log.info(f"Time to Start Sync - as current hour: {current_time.hour} in schedule {str(self.sync_start_times)}")
                self.master_queue()

                current_time = datetime.datetime.now()
                next_hour = (current_time + datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=1)
                sleep_seconds = (next_hour - current_time).total_seconds()

                self.log.info(f"Sync Complete - Sleeping for {int(sleep_seconds)} seconds until {next_hour.time()}")
                time.sleep(sleep_seconds)
                self.log.info(f"Checking sync schedule every 10 minutes: {str(self.sync_start_times)}")

            else:
                time.sleep(600)

    def get_list_of_videos_from_youtube(self, channel, current_channel_files):
        # TODO: It would be nice to cache individual video metadata so we don't have to re-download it. This does not
        #       really matter in the production, but it really sucks to re-download same stuff again and again during
        #       the development. Not to mention that this is probably the reason my IP got blacklisted by YT.

        days_to_retrieve = channel["DL_Days"]
        channel_link = channel["Link"]
        search_limit = channel["Search_Limit"]
        video_to_download_list = []

        ydl_opts = {
            "quiet": True,
            "extract_flat": True,
            "match_filter": video_duration_filter,
        }
        ydl_opts |= self.ytd_extra_parameters

        if self.youtube_slow is True:
            ydl_opts |= self.ytd_slow_parameters

        if search_limit > 0:
            ydl_opts["playlist_items"] = f"1-{search_limit}"
        ydl = yt_dlp.YoutubeDL(ydl_opts)

        playlist = ydl.extract_info(channel_link, download=False)
        channel_title = playlist.get("title")
        channel_id = playlist.get("channel_id")
        self.log.info(f"{channel["Name"]}> CHANNEL_ID={channel_id}  VIDEOS={len(playlist["entries"])}  TITLE='{channel_title}' ")

        if "playlist?list" not in channel_link.lower():
            if not channel_id:
                raise Exception("No Channel ID")
            if not channel_title:
                raise Exception("No Channel Title")

            if channel["Live_Rule"] == "Only":
                self.log.info(f"{channel["Name"]}> Getting list of live videos for this channel")
                playlist_url = f"{channel_link}/streams"
            else:
                self.log.info(f"{channel["Name"]}> Getting list of videos")
                playlist_url = f"https://www.youtube.com/playlist?list=UU{channel_id[2:]}"

            playlist = ydl.extract_info(playlist_url, download=False)

        today = datetime.datetime.now()
        cutoff_date = None if days_to_retrieve == -1 else today - datetime.timedelta(days=days_to_retrieve)

        fails = 0
        for video in playlist["entries"]:
            try:
                video_title = f'{video["title"]} [{video["id"]}]' if self.include_id_in_filename else video["title"]
                duration = 0 if not video["duration"] else video["duration"]

                if channel["Live_Rule"] == "Only":
                    if len(video_to_download_list):
                        self.log.info(f"{channel["Name"]}|{video["id"]}> Live video found")
                        self.log.info(f"{channel["Name"]}|{video["id"]}> Downloading only first live video")
                        break

                    if video["live_status"] == "is_upcoming":
                        self.log.info(f"{channel["Name"]}|{video["id"]}> Skipping upcoming live video: {video_title}")
                        continue

                    if video["live_status"] not in ("is_live", "post_live"):
                        self.log.info(f"{channel["Name"]}|{video["id"]}> We only want active live videos and none were found")
                        break

                if channel["Live_Rule"] == "Ignore" and video["live_status"] is not None:
                    self.log.info(f"{channel["Name"]}|{video["id"]}> Ignoring live video: {video_title}")
                    continue

                if video["id"] in current_channel_files["id_list"] or video_title in current_channel_files["filename_list"]:
                    self.log.info(f"{channel["Name"]}|{video["id"]}> File for video '{video_title}' already in folder.")
                    continue

                self.log.info(f"{channel["Name"]}|{video["id"]}> Extracting info for '{video_title}' ({duration}s long)")
                video_extracted_info = ydl.extract_info(video["url"], download=False)

                video_upload_date_raw = video_extracted_info["upload_date"]
                video_upload_date = datetime.datetime.strptime(video_upload_date_raw, "%Y%m%d")
                video_timestamp = video_extracted_info["timestamp"]

                current_time = time.time()
                age_in_hours = (current_time - video_timestamp) / 3600

                if cutoff_date is not None:
                    if video_upload_date < cutoff_date:
                        self.log.info(f"{channel["Name"]}|{video["id"]}> Ignoring video as it is older than the cut-off {cutoff_date}.")
                        self.log.info(f"{channel["Name"]}|{video["id"]}> No more videos in date range")
                        break

                if age_in_hours < self.defer_hours and video["live_status"] is None:
                    self.log.info(f"{channel["Name"]}|{video["id"]}> Video is {age_in_hours:.2f} hours old. Waiting until it's older than {self.defer_hours} hours.")
                    continue

                if channel.get("Filter_Title_Text"):
                    if channel["Negate_Filter"] and channel["Filter_Title_Text"].lower() in video_title.lower():
                        self.log.info(f"{channel["Name"]}|{video["id"]}> Skipped video as it contains the filter text: {channel["Filter_Title_Text"]}")
                        continue

                    if not channel["Negate_Filter"] and channel["Filter_Title_Text"].lower() not in video_title.lower():
                        self.log.info(f"{channel["Name"]}|{video["id"]}> Skipped video as it does not contain the filter text: {channel["Filter_Title_Text"]}")
                        continue

                video_to_download_list.append(
                        {
                            "title": video_title,
                            "upload_date": video_upload_date,
                            "link": video["url"],
                            "id": video["id"],
                            "channel_name": channel_title
                        })
                self.log.info(f"{channel["Name"]}|{video["id"]}> Added video to download list")
                fails = 0
            except Exception as e:
                fails += 1
                self.log.error(f"{channel["Name"]}|{video["id"]}> Error extracting details: {str(e)}")

                if fails >= 3:
                    self.log.error(f"{channel["Name"]}> Too many errors in succession, aborting! Please check logs.")
                    return None

        return video_to_download_list

    def get_list_of_files_from_channel_folder(self, channel_folder_path):
        folder_info = { "id_list": [], "filename_list": [] }
        try:
            raw_directory_list = os.listdir(channel_folder_path)
            for filename in raw_directory_list:
                file_path = os.path.join(channel_folder_path, filename)
                if not os.path.isfile(file_path):
                    continue

                try:
                    file_base_name, file_ext = os.path.splitext(filename)
                    id_in_title = re.search(r"\[([0-9A-Za-z_-]{10,}[048AEIMQUYcgkosw])\]", file_base_name)
                    if id_in_title:
                        if file_ext.lower() in MEDIA_FILE_EXTENSIONS:
                            folder_info["id_list"].append(id_in_title.group(1))
                            folder_info["filename_list"].append(file_base_name)

                    elif file_ext.lower() in MEDIA_FILE_EXTENSIONS:
                        folder_info["filename_list"].append(file_base_name)
                        mp4_file = MP4(file_path)
                        embedded_video_id = mp4_file.get("\xa9cmt", [None])[0]
                        folder_info["id_list"].append(embedded_video_id)

                except Exception as e:
                    self.log.error(f"No video ID present or cannot read it from metadata of {filename}: {e}")

        except Exception as e:
            self.log.error(f"Error getting list of files for channel folder: {e}")

        finally:
            self.log.info(f'Found {len(folder_info["filename_list"])} files and {len(folder_info["id_list"])} IDs in {channel_folder_path}.')
            return folder_info

    def count_media_files_for_channel( self, channel ) -> tuple[int, int]:
        channel_folder_path = self.audio_download_folder if channel["Audio_Only"] else self.download_folder
        channel_folder_path = os.path.join(channel_folder_path, channel["Name"])

        if not os.path.isdir(channel_folder_path) or channel["Name"] == "":
            return -1, 0

        return self.count_media_files(channel_folder_path)

    def count_media_files(self, channel_folder_path) -> tuple[int, int]:
        video_item_count = 0
        audio_item_count = 0
        files_size = 0

        raw_directory_list = os.listdir(channel_folder_path)
        for filename in raw_directory_list:
            file_path = os.path.join(channel_folder_path, filename)
            if not os.path.isfile(file_path):
                continue

            files_size += os.path.getsize(file_path)

            file_base_name, file_ext = os.path.splitext(filename.lower())
            if file_ext in VIDEO_EXTENSIONS:
                video_item_count += 1
            elif file_ext in AUDIO_EXTENSIONS:
                audio_item_count += 1

        self.log.info(f"count_media_files|{channel_folder_path}> Found {video_item_count} video files and {audio_item_count} audio files, totalling {number_si_suffix(files_size)}B")

        return video_item_count + audio_item_count, files_size

    def cleanup_old_files(self, channel_folder_path, channel):
        days_to_keep = channel["Keep_Days"]

        if days_to_keep == PERMANENT_RETENTION:
            self.log.info(f"{channel['Name']}> Skipping cleanup due to permanent retention policy.")
            return

        current_datetime = datetime.datetime.now()
        raw_directory_list = os.listdir(channel_folder_path)
        for filename in raw_directory_list:
            try:
                file_path = os.path.join(channel_folder_path, filename)
                if not os.path.isfile(file_path):
                    continue

                file_base_name, file_ext = os.path.splitext(filename.lower())

                video_file_check = file_ext in VIDEO_EXTENSIONS and not channel["Audio_Only"]
                audio_file_check = file_ext in AUDIO_EXTENSIONS and channel["Audio_Only"]
                subtitle_file_check = file_ext == ".srt" and self.subtitles == "external"

                if not (video_file_check or audio_file_check or subtitle_file_check):
                    continue

                file_mtime = self.get_file_modification_time(file_path, filename, file_ext)
                age = current_datetime - file_mtime

                if age > datetime.timedelta(days=days_to_keep):
                    os.remove(file_path)
                    self.log.info(f"{channel['Name']}> Deleted '{filename}' as it is {age.days} days old.")
                    self.media_server_scan_req_flag = True
                else:
                    self.log.info(f"{channel['Name']}> File '{filename}' is {age.days} days old, keeping file as not over {days_to_keep} days.")

            except Exception as e:
                self.log.error(f"{channel['Name']}> Error Cleaning Old Files: {filename} {str(e)}")

    def get_file_modification_time(self, file_path, filename, file_ext):
        try:
            if file_ext == ".srt":
                file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
                return file_mtime

            mpeg4_file = MP4(file_path)
            mpeg4_file_created_timestamp = mpeg4_file.get("\xa9day", [None])[0]
            if mpeg4_file_created_timestamp:
                file_mtime = datetime.datetime.strptime(mpeg4_file_created_timestamp, "%Y-%m-%d %H:%M:%S")
                self.log.info(f"Extracted datetime {file_mtime} from metadata of {filename}")
                return file_mtime
            else:
                raise Exception("No timestamp found")

        except Exception as e:
            self.log.warning(f"Error extracting datetime from metadata for {filename}: {e}")
            file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
            self.log.warning(f"Using filesystem modified timestamp {file_mtime} for {filename}")
            return file_mtime

    def download_items(self, item_list, channel_folder_path, channel):
        fails = 0
        temp_dir = None
        current_item = 0
        for item in item_list:
            current_item += 1

            self.log.info(f"{channel["Name"]}|{item["id"]}> Processing download of '{item["title"]}' [{current_item}/{len(item_list)}]")

            try:
                temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
                link = item["link"]
                cleaned_title = self.string_cleaner(item["title"])
                #selected_media_type = channel["Media_Type"]
                post_processors = []

                if channel["Use_SponsorBlock"]:
                    post_processors.append(
                        [
                            {"key": "SponsorBlock", "categories": ["sponsor"]},
                            {"key": "ModifyChapters", "remove_sponsor_segments": ["sponsor"]}
                        ]
                    )

                if channel["Audio_Only"]:
                    if channel["Use_Best_Quality"]:
                        # Format that contains video, and if it doesn't already have an audio stream, merge it with best audio-only format
                        selected_format = f"bestaudio/best"
                        merge_output_format = None
                        post_processors.append( { "key": "FFmpegExtractAudio", "preferredquality": 0, } )
                    else:
                        selected_ext = "m4a"
                        selected_format = f"{self.audio_format_id}/bestaudio[acodec^={self.fallback_acodec}]/bestaudio"
                        merge_output_format = None
                        post_processors.append( { "key": "FFmpegExtractAudio", "preferredcodec": selected_ext, "preferredquality": 0, } )


                else:
                    if channel["Use_Best_Quality"]:
                        # Format that contains video, and if it doesn't already have an audio stream, merge it with best audio-only format
                        selected_format = f"bestvideo*+bestaudio/best"
                        merge_output_format = None
                    else:
                        selected_ext = "mp4"
                        selected_format = f"{self.video_format_id}+{self.audio_format_id}/bestvideo[vcodec^={self.fallback_vcodec}]+bestaudio[acodec^={self.fallback_acodec}]/bestvideo+bestaudio/best"
                        merge_output_format = selected_ext

                post_processors.extend(
                    [
                        {"key": "FFmpegMetadata"},
                        {"key": "EmbedThumbnail"},
                    ]
                )

                #folder_and_filename = os.path.join(channel_folder_path, cleaned_title)
                ydl_opts = {
                    "paths": {"home": channel_folder_path, "temp": temp_dir.name},
                    "format": selected_format,
                    "outtmpl": f"{cleaned_title}.%(ext)s",
                    "writethumbnail": True,
                    "progress_hooks": [self.progress_callback],
                    "postprocessors": post_processors,
                    "no_mtime": channel["Set_Mtime"],
                    "live_from_start": True,
                    "extractor_args": {"youtubetab": {"skip": ["authcheck"]}},
                    "writeinfojson": channel["Write_Info_Json"],
                }
                ydl_opts |= self.ytd_extra_parameters

                if self.youtube_slow is True:
                    ydl_opts |= self.ytd_slow_parameters

                if self.subtitles in ["embed", "external"]:
                    ydl_opts.update(
                        {
                            "subtitlesformat": "best",
                            "writeautomaticsub": True,
                            "writesubtitles": True,
                            "subtitleslangs": self.subtitle_languages,
                        }
                    )
                    if self.subtitles == "embed":
                        post_processors.extend([{"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False}])
                    elif self.subtitles == "external":
                        post_processors.extend([{"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"}])

                if merge_output_format:
                    ydl_opts["merge_output_format"] = merge_output_format

                yt_downloader = yt_dlp.YoutubeDL(ydl_opts)
                self.log.info(f"{channel["Name"]}|{item["id"]}> Download parameters: {ydl_opts}")
                self.log.info(f"{channel["Name"]}|{item["id"]}> Starting yt-dlp")
                self.download_progress_report_perc = 0
                yt_ret = yt_downloader.download([link])
                self.log.info(f"{channel["Name"]}|{item["id"]}> yt-dlp finished with return code {yt_ret}")


                #self.add_extra_metadata(f"{folder_and_filename}.{selected_ext}", item)

                # Update media counter so we can see progress in GUI
                itm_count, itm_size = self.count_media_files(channel_folder_path)
                channel.update( { "Item_Count": itm_count, "Item_Size": itm_size } )
                self.emit_channel_refresh()

                fails = 0
            except Exception as e:
                fails += 1
                self.log.error(f"{channel["Name"]}|{item["id"]}> Error downloading video: {link}. Error message: {e}")

                if fails >= 3:
                    self.log.error(f"{channel["Name"]}> Too many errors in succession, aborting! Please check logs.")
                    return False

        if temp_dir is not None:
            temp_dir.cleanup()
        return True

    def progress_callback(self, progress_data):
        status = progress_data.get("status", "unknown")
        is_live_video = progress_data.get("info_dict", {}).get("is_live", False)
        fragment_index = progress_data.get("fragment_index", 1)
        show_live_log_message = fragment_index % 10 == 0
        elapsed = progress_data.get("elapsed", 1)
        percent = int(progress_data.get("_percent", 0))
        minutes, seconds = divmod(elapsed, 60)

        if status == "finished":
            self.log.info("Finished downloading video")

        elif status == "downloading":
            if is_live_video:
                if not show_live_log_message:
                    return
                downloaded_bytes_str = progress_data.get("_downloaded_bytes_str", "0")
                elapsed_str = f"{int(minutes)} minutes and {int(seconds)} seconds"
                self.log.info(f"Live Video - Downloaded: {downloaded_bytes_str} (Fragment Index: {fragment_index}, Elapsed: {elapsed_str})")

            else:
                # Display progress message only once each 5%
                if percent < self.download_progress_report_perc:
                    return

                self.download_progress_report_perc = (int(percent/5)+1)*5

                percent_str = progress_data.get("_percent_str", "unknown")
                total_bytes_str = progress_data.get("_total_bytes_str", "unknown")
                speed_str = progress_data.get("_speed_str", "unknown")
                eta_str = progress_data.get("_eta_str", "unknown")

                self.log.info(f"Downloaded {percent_str} of {total_bytes_str} at {speed_str} with ETA {eta_str}")

    def add_extra_metadata(self, file_path, item):
        try:
            current_datetime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            m4_file = MP4(file_path)
            m4_file["\xa9day"] = current_datetime
            m4_file["\xa9cmt"] = item["id"]
            m4_file["\xa9nam"] = item["title"]
            m4_file["\xa9ART"] = item["channel_name"]
            m4_file["\xa9gen"] = item["channel_name"]
            m4_file["\xa9pub"] = item["channel_name"]
            m4_file.save()
            self.log.info(f'Added timestamp: {current_datetime} and video ID: {item["id"]} to metadata of: {file_path}')

        except Exception as e:
            self.log.error(f"Error adding metadata to {file_path}: {e}")

    def master_queue(self):
        if self.task_thread_started:
            self.log.info("Sync Task already running, not starting another one until it finishes")
            return

        sync_eligible_channels = 0
        try:
            self.task_thread_started = True
            self.media_server_scan_req_flag = False
            self.process_channel_errors = 0
            self.log.warning(f"Sync Task started for {len(self.req_channel_list)} channels")
            socketio.emit("sync_state_changed", { "Sync_State": "run" })

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.thread_limit) as executor:
                futures = []
                for channel in self.req_channel_list:
                    if self.process_channel_errors >= 3:
                        raise Exception("Too many errors, aborting sync! Please check logs.")

                    if channel.get("Last_Synced") in ["In Progress", "Queued"]:
                        self.log.info(f"queue|{channel["Name"]}> Channel synchronization already in progress")
                        continue

                    if channel.get("Paused") == 1:
                        self.log.info(f"queue|{channel["Name"]}> Channel paused, skipping")
                        continue

                    self.log.info(f"queue|{channel["Name"]}> Adding channel to sync queue")
                    channel["Last_Synced"] = "Queued"
                    sync_eligible_channels += 1
                    futures.append(executor.submit(self.process_channel, channel))
                self.emit_channel_refresh()
            concurrent.futures.wait(futures)

            if self.req_channel_list:
                self.save_channel_list_to_file()
            else:
                self.log.warning("Channel list empty")

            if self.media_server_scan_req_flag is True and self.media_server_tokens:
                self.sync_media_servers()
            else:
                self.log.info("Media Server Sync not required")

        except Exception as e:
            self.log.error(f"Sync error: {str(e)}")

        if self.process_channel_errors >= 3 or self.process_channel_errors == sync_eligible_channels:
            self.log.error(f"Sync failed completely ({self.process_channel_errors}/{sync_eligible_channels} failed)")
            socketio.emit("sync_state_changed", {"Sync_State": "stop", "Success": False})

        elif self.process_channel_errors > 0:
            self.log.warning(f"Sync finished with some errors ({self.process_channel_errors}/{sync_eligible_channels} failed)")
            socketio.emit("sync_state_changed", {"Sync_State": "stop", "Success": True})

        else:
            self.log.info(f"Sync Finished: Completed all {sync_eligible_channels} channels")
            socketio.emit("sync_state_changed", {"Sync_State": "stop", "Success": True})

        self.emit_channel_refresh()
        self.task_thread_started = False

    def process_channel(self, channel):
        sync_encountered_problem = False
        try:
            channel["Last_Synced"] = "In Progress"
            channel_folder_path = os.path.join(self.audio_download_folder, channel["Name"]) if channel["Audio_Only"] else os.path.join(self.download_folder, channel["Name"])
            os.makedirs(channel_folder_path, exist_ok=True)

            self.log.info(f'{channel["Name"]}> Getting current list of files for channel from {channel_folder_path}')
            current_channel_files = self.get_list_of_files_from_channel_folder(channel_folder_path)

            self.log.info(f'{channel["Name"]}> Getting list of videos from {channel["Link"]}')
            item_download_list = self.get_list_of_videos_from_youtube(channel, current_channel_files)

            # We generally don't bail out when we increase process_channel_errors counter because we still want to
            # continue & clean up old files etc...
            if item_download_list is None:
                self.process_channel_errors += 1
                sync_encountered_problem = True
                self.log.warning(f'{channel["Name"]}> FAILED to get list of videos from YT, increasing error counter to {self.process_channel_errors}')

            elif item_download_list:
                channel["Remote_Count"] = len(item_download_list)
                self.log.info(f'{channel["Name"]}> Downloading video list')
                if not self.download_items(item_download_list, channel_folder_path, channel):
                    self.process_channel_errors += 1
                    sync_encountered_problem = True
                    self.log.warning(f'{channel["Name"]}> FAILED downloading videos for channel, increasing error counter to {self.process_channel_errors}')

                else:
                    self.log.info(f'{channel["Name"]}> Finished downloading videos for channel')

                self.media_server_scan_req_flag = True

            else:
                self.log.warning(f'{channel["Name"]}> No videos to download')
                channel["Remote_Count"] = 0

            self.log.info(f'{channel["Name"]}> Clearing old files')
            self.cleanup_old_files(channel_folder_path, channel)
            self.log.info(f'{channel["Name"]}> Finished clearing old files, recounting files...')
            itm_count, itm_size = self.count_media_files(channel_folder_path)
            channel["Item_Count"] = itm_count
            channel["Item_Size"] = itm_size

        except Exception as e:
            self.log.error(f'{channel["Name"]}> Error processing channel: {str(e)}')
            sync_encountered_problem = True

        finally:
            # TODO: Update Last_Synced only on successful sync - right now the logic is not really ideal, when Sync
            #       starts it changes Last_Synced to "In Progress" and saves it to the channel_list.json... So we really
            #       *have* to set it like this for now. But I want to fix this properly...

            if not sync_encountered_problem:
                self.log.info(f'{channel["Name"]}> Channel processed')
                channel["Last_Synced"] = datetime.datetime.now().strftime("%d-%m-%y %H:%M:%S")
            else:
                self.log.warning(f'{channel["Name"]}> Channel processed with some problems (check logs).')
                channel["Last_Synced"] = "Failed"
            self.emit_channel_refresh()

    def add_channel(self):
        existing_ids = [channel.get("Id", 0) for channel in self.req_channel_list]
        next_id = max(existing_ids, default=-1) + 1
        new_channel = {
            "Id": next_id,
            "Name": "New Channel",
            "Link": "https://www.youtube.com/@NewChannel",
            "Keep_Days": 28,
            "DL_Days": 14,
            "Last_Synced": "Never",
            "Item_Count": 0,
            "Item_Size": 0,
            "Remote_Count": 0,
            "Filter_Title_Text": "",
            "Negate_Filter": False,
            "Audio_Only": False,
            "Search_Limit": 0,
            "Live_Rule": "Ignore",
            "Write_Info_Json": True,
            "Set_Mtime": True,
        }
        self.req_channel_list.append(new_channel)
        socketio.emit("new_channel_added", new_channel)
        self.save_channel_list_to_file()

    def emit_channel_refresh( self ):
        socketio.emit("update_channel_list", {"Channel_List": self.req_channel_list})

    def remove_channel(self, channel_to_be_removed):
        self.req_channel_list = [channel for channel in self.req_channel_list if channel["Id"] != channel_to_be_removed["Id"]]
        self.save_channel_list_to_file()

    def sync_media_servers(self):
        media_servers = self.convert_string_to_dict(self.media_server_addresses)
        media_tokens = self.convert_string_to_dict(self.media_server_tokens)
        if "Plex" in media_servers and "Plex" in media_tokens:
            try:
                token = media_tokens.get("Plex")
                address = media_servers.get("Plex")
                self.log.warning("Attempting Plex Sync")
                media_server_server = PlexServer(address, token)
                library_section = media_server_server.library.section(self.media_server_library_name)
                library_section.update()
                self.log.info(f"Plex Library scan for '{self.media_server_library_name}' started.")
            except Exception as e:
                self.log.info(f"Plex Library scan failed: {str(e)}")

        if "Jellyfin" in media_servers and "Jellyfin" in media_tokens:
            try:
                token = media_tokens.get("Jellyfin")
                address = media_servers.get("Jellyfin")
                self.log.info("Attempting Jellyfin Sync")
                url = f"{address}/Library/Refresh?api_key={token}"
                response = requests.post(url)
                if response.status_code == 204:
                    self.log.info("Jellyfin Library refresh request successful.")
                else:
                    self.log.info(f"Jellyfin Error: {response.status_code}, {response.text}")
            except Exception as e:
                self.log.info(f"Jellyfin Library scan failed: {str(e)}")

    def string_cleaner(self, input_string):
        if isinstance(input_string, str):
            raw_string = re.sub(r'[\/:*?"<>|]', " ", input_string)
            temp_string = re.sub(r"\s+", " ", raw_string)
            cleaned_string = temp_string.strip()
            return cleaned_string

        elif isinstance(input_string, list):
            cleaned_strings = []
            for string in input_string:
                file_name_without_extension, file_extension = os.path.splitext(string)
                raw_string = re.sub(r'[\/:*?"<>|]', " ", file_name_without_extension)
                temp_string = re.sub(r"\s+", " ", raw_string)
                cleaned_string = temp_string.strip()
                cleaned_strings.append(cleaned_string)
            return cleaned_strings
        return None

    def convert_string_to_dict(self, raw_string):
        result = {}
        if not raw_string:
            return result

        pairs = raw_string.split(",")
        for pair in pairs:
            key_value = pair.split(":", 1)
            if len(key_value) == 2:
                key, value = key_value
                result[key.strip()] = value.strip()

        return result

    def save_settings(self, data):
        self.media_server_addresses = data["media_server_addresses"]
        self.media_server_tokens = data["media_server_tokens"]
        self.media_server_library_name = data["media_server_library_name"]
        self.ignore_ssl_errors = data["ignore_ssl_errors"]
        self.youtube_slow = data["youtube_slow"]

        try:
            if data["sync_start_times"] == "":
                self.sync_start_times = []
            else:
                raw_sync_start_times = [int(re.sub(r"\D", "", start_time.strip())) for start_time in data["sync_start_times"].split(",")]
                temp_sync_start_times = [0 if x < 0 or x > 23 else x for x in raw_sync_start_times]
                cleaned_sync_start_times = sorted(list(set(temp_sync_start_times)))
                self.sync_start_times = cleaned_sync_start_times

        except Exception as e:
            self.log.error(f"Error Updating Settings: {str(e)}")
            self.sync_start_times = []

        finally:
            self.log.info(f"Sync Hours: {str(self.sync_start_times)}")
            self.save_settings_to_file_and_reload()

    def save_channel_changes(self, channel_to_be_saved):
        try:
            # Remove fields that we don't want to be updatable from WebGUI
            for rem in ("Item_Count", "Item_Size", "Last_Synced", "Remote_Count"):
                channel_to_be_saved.pop(rem, None)

            for channel in self.req_channel_list:
                if channel["Id"] == channel_to_be_saved.get("Id"):
                    channel.update(channel_to_be_saved)
                    self.log.info(f"{channel_to_be_saved.get('Name')}> Channel saved.")
                    break
            else:
                self.log.warning(f"{channel_to_be_saved.get('Name')}> Channel not found.")

        except Exception as e:
            self.log.error(f"{channel_to_be_saved.get('Name')}> Error saving channel: {str(e)}")
            return False

        else:
            self.save_channel_list_to_file()
            return True

    def manual_start(self):
        if not self.task_thread_started:
            self.log.warning("Manual sync triggered.")
            self.task_thread = threading.Thread(target=self.master_queue, daemon=True)
            self.task_thread.start()
        else:
            self.log.warning("Cannot trigger manual sync, previous sync still running...")

        #socketio.emit("settings_save_message", "Manual sync initiated.")


app = Flask(__name__)
app.secret_key = os.urandom(12).hex()
socketio = SocketIO(app)
data_handler = DataHandler()


@app.route("/")
def home():
    return render_template("base.html")


@socketio.on("connect")
def connection():
    socketio.emit("update_channel_list", {"Channel_List": data_handler.req_channel_list})
    socketio.emit("sync_state_changed", { "Sync_State": "run" if data_handler.task_thread_started else "stop" })


@socketio.on("get_settings")
def get_settings():
    data = {
        "sync_start_times": data_handler.sync_start_times,
        "media_server_addresses": data_handler.media_server_addresses,
        "media_server_tokens": data_handler.media_server_tokens,
        "media_server_library_name": data_handler.media_server_library_name,
        "ignore_ssl_errors": data_handler.ignore_ssl_errors,
        "youtube_slow": data_handler.youtube_slow,
    }
    socketio.emit("current_settings", data)


@socketio.on("save_channel_changes")
def save_channel_changes(channel_to_be_saved):
    data_handler.save_channel_changes(channel_to_be_saved)
    socketio.emit("channel_save_message", "Channel Settings Saved Successfully.")


@socketio.on("save_settings")
def save_settings(data):
    data_handler.save_settings(data)
    socketio.emit("settings_save_message", "Settings Saved Successfully.")


@socketio.on("add_channel")
def add_channel():
    data_handler.add_channel()


@socketio.on("pause_channel")
def pause_channel(channel_to_be_paused):
    if data_handler.save_channel_changes(channel_to_be_paused):
        socketio.emit("channel_save_message", "Channel paused")
    else:
        socketio.emit("channel_save_message", "Failed to pause channel")

    socketio.emit("update_channel_list", {"Channel_List": data_handler.req_channel_list})


@socketio.on("remove_channel")
def remove_channel(channel_to_be_removed):
    data_handler.remove_channel(channel_to_be_removed)


@socketio.on("sync_toggle")
def manual_start():
    data_handler.log.warning(f"Manual synchronization toggle")
    data_handler.manual_start()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)

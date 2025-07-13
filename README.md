![Build Status](https://github.com/Xoores/ArchiveTube/actions/workflows/main.yml/badge.svg)
![Docker Pulls](https://img.shields.io/docker/pulls/xoores/archivetube.svg)


![full_logo](https://raw.githubusercontent.com/Xoores/ArchiveTube/main/src/static/full_logo.png)


ArchiveTube is a tool for synchronizing and fetching content from YouTube channels using yt-dlp, based on [ChannelTube](https://github.com/TheWicklowWolf/ChannelTube).

## WTF (Why the Fork)?

I needed something to automatically *archive* selected YouTube channels in case they get deleted or lost over time. [ChannelTube](https://github.com/TheWicklowWolf/ChannelTube) *almost* fit my requirements, but not quite. It is obvious, that its use case is a little bit different from what I needed.

I tried to open a new feature issue and found out that the original maintainer had an autoclose bot set up, stating that he does not intend to enhance features of the project as he is satisfied with the current state and no major changes will be accepted.

So I forked the original project and tweaked it to my liking - and if you want, you can use it, too.

Some differences over ChannelTube:
#### Friendlier config for archiving use case
Originally ChannelTube did not really provide a friendly way for archivation use case. Example: for each channel added, you needed to manually specify max "Days to Sync" field by putting in some large number and after initial (successful) synchronization you'd have to change it to something sane so it does not slow down ChannelTube too much.

I fixed that and added few more options to make channel archiving easier.

#### Best quality download by default
I added a simple toggle button that allows you to either keep the original logic of format/quality selection or just download the best quality possible. Configurable per-channel.

#### Better control over downloads
I added few more controls that control specific behavior of the yt-dlp downloader (per channel).

Notably:
- Checkbox to control SponsorBlock filtering. ChannelTube had it hardcoded by default, which is not necessarily something you want to do when archiving videos.
- Ability to save metadata to separate .json file that you can easily search through if necessary.
- Checkbox to enable mtime modification for downloaded files. Again, useful for archiving purposes.
- Ability to "pause" synchronization of each channel, so it is skipped in automatic re-sync. No need to delete and re-add the channel anymore.

#### Self-contained
ArchiveTube use several libraries and CSS styles such as Bootstrap, Font Awesome and socket.io. ArchiveTube caches all these necessary files locally so there are no external connections made when accessing the GUI.

Downloading from YouTube does require external connection, however my target is to limit the amount of external dependencies as much as possible.

## Run using docker-compose

```yaml
services:
  channeltube:
    image: xoores/archivetube:latest
    container_name: archivetube
    volumes:
      - /path/to/config:/archivetube/config
      - /data/media/video:/archivetube/downloads
      - /data/media/audio:/archivetube/audio_downloads
      - /etc/localtime:/etc/localtime:ro
    ports:
      - 5000:5000
    restart: unless-stopped
```

## Configuration via environment variables

Certain values can be set via environment variables:

* __PUID__: The user ID to run the app with. Defaults to `1000`. 
* __PGID__: The group ID to run the app with. Defaults to `1000`.
* __video_format_id__: Specifies the ID for the video format. The default value is `137`.
* __audio_format_id__: Specifies the ID for the audio format. The default value is `140`.
* __defer_hours__: Defines the time to defer in hours. The default value is `0`.
* __thread_limit__: Sets the maximum number of threads to use. The default value is `1`.
* __fallback_vcodec__: Specifies the fallback video codec to use. Defaults to `vp9`.  
* __fallback_acodec__ :Specifies the fallback audio codec to use. Defaults to `mp4a`.  
* __subtitles__: Controls subtitle handling. Options: `none`, `embed`, `external`. Defaults to `none`.
* __subtitle_languages__: Comma-separated list of subtitle languages to include. Defaults to `en`.
* __verbose_logs__: Enable verbose logging. Set to `true` or `false`. Defaults to `false`.

Removed:
* __include_id_in_filename__: Include Video ID in filename, now permanently enabled as download formats are not limited to MP4 by default. And I was lazy to implement MKV/* parser for metadata.


> For information on format IDs, refer to [https://github.com/yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp)
> 
> ![yt-dlp-formats](https://github.com/user-attachments/assets/e03b9dd3-028f-4c72-b822-06aa1d440cea)


## Sync Schedule

Use a comma-separated list of hours to search for new items (e.g. `2, 20` will initiate a search at 2 AM and 8 PM).
> Note: There is a deadband of up to 10 minutes from the scheduled start time.

## Media Server Integration (optional)

A media server library scan can be triggered when new content is retrieved.

For Plex, use: `Plex: http://192.168.1.2:32400`  
For Jellyfin, use: `Jellyfin: http://192.168.1.2:8096`  
To use both, enter: `Plex: http://192.168.1.2:32400, Jellyfin: http://192.168.1.2:8096`  
The same format applies for the tokens.  

The **Media Server Library Name** refers to the name of the library where the videos are stored.  

To disable this feature:
- Leave **Media Server Addresses**, **Media Server Tokens** and **Media Server Library Name** blank.  

## Cookies (optional)
To utilize a cookies file with yt-dlp, follow these steps:

* Generate Cookies File: Open your web browser and use a suitable extension (e.g. cookies.txt for Firefox) to extract cookies for a user on YT.

* Save Cookies File: Save the obtained cookies into a file named `cookies.txt` and put it into the config folder.


---

![light](https://raw.githubusercontent.com/Xoores/ArchiveTube/main/src/static/light.png)


---


![dark](https://raw.githubusercontent.com/Xoores/ArchiveTube/main/src/static/dark.png)

---


https://hub.docker.com/r/thewicklowwolf/channeltube

#!/bin/sh
# shellcheck disable=SC3037 # We are in busybox, so echo flags actually exist...

PUID=${PUID:-1000}
PGID=${PGID:-1000}
export XDG_CACHE_HOME=/archivetube/cache

echo -e "\n\e[1;44m                                     ArchiveTube by Xoores                                     \e[0m"
echo -e "                         https://github.com/xoores/ArchiveTube\n"
echo -e "Tool for synchronizing, fetching and archiving content from YouTube channels using yt-dlp.\n\n"
echo -e "\e[1;44m     System info     \e[0m"
echo -n " - id: "; id
echo -n " - kernel: "; uname -a
echo -e "\e[1mApplications:\e[0m"
echo -n " - yt-dlp: "; pip show yt-dlp | grep Version: | awk '{print $2}'
echo -n " - ffmpeg: "; ffmpeg -version | head -n 1 | awk '{print $3}'
echo -n " - python: "; python --version | cut -d' ' -f2-
echo -n " - gunicorn: "; gunicorn --version | cut -d'(' -f2 | cut -d' ' -f2 | cut -d')' -f1
echo -e " - ArchiveTube: ${RELEASE_VERSION:-?}"

echo -e "\e[1mEnvironment:\e[0m"

for V in PWD SHELL PUID PGID video_format_id audio_format_id defer_hours thread_limit fallback_vcodec fallback_acodec subtitles \
         subtitle_languages verbose_logs; do
  echo -n " - ${V}"
  eval VAL="\$${V}"
  if [ ${#VAL} -eq 0 ]; then
    echo -e " \e[3;33mis not set\e[0m"
  else
    echo "=${VAL}"
  fi
done

if [ ${#include_id_in_filename} -ne 0 ]; then
  echo "WARNING: include_id_in_filename is deprecated (all downloaded videos will contain ID in filename now)"
fi

echo "Setting up directories.."
mkdir -p /archivetube/downloads /archivetube/audio_downloads /archivetube/config /archivetube/cache
chown -R "${PUID}:${PGID}" /archivetube


echo "Running ArchiveTube..."
exec su-exec "${PUID}:${PGID}" gunicorn src.ArchiveTube:app -c gunicorn_config.py

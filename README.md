# bandcamp-downloader [![Python package](https://github.com/easlice/bandcamp-downloader/actions/workflows/python-package.yml/badge.svg)](https://github.com/easlice/bandcamp-downloader/actions/workflows/python-package.yml)
Download your Bandcamp collection using this python script.

It requires you to have a browser with a logged in session of bandcamp open. Cookies from the browser will be used to authenticate with Bandcamp.

Supported browsers are the same as in [browser_cookie3](https://github.com/borisbabic/browser_cookie3): Chrome, Chromium, Firefox, Brave, Opera, and Edge

Alternatively, you can use a [Netscape format cookies](https://curl.se/docs/http-cookies.html) file.

Albums will be downloaded into their zip files and singles will just be plain files. Downloads are organized by Artist name. Already existing files of the same name will have their file sizes checked against what it should be, and if they are the same, the download will be skipped, otherwise it will be over-written. You can use the `--force` flag to always overwrite existing files.

By default the download only includes unhidden items in your collection. To include hidden items as well, use the `--include-hidden` flag.

Downloads will happen in parallel, by default using a pool of 5 threads. You can configure how many threads to use with the `--parallel-downloads`/`-p` flag. After each download a thread will wait 1 second before trying the next download. This is to try and not overwhelm (and be rejected by) the bandcamp servers. This can be configured with the `--wait-after-download` flag.

If a download should fail because of an HTTP/network error, it will be retried again after a short wait. By default a file download will be attempted at most 5 times. This can be configured with the `--max-download-attempts` flag. By default, a failed download will wait 5 seconds before trying again. This can be configured by the `--retry-wait` flag.

By default, files are downloaded in mp3-320 format, but that can be changed with the `--format`/`-f` flag.

## Known Issues

### Running the script on WSL crashes with a `DBUS_SESSION_BUS_ADDRESS` error

This is seems to be a WSL issue. The browser_cookie3 module  tries to get a secret from your keyring via dbus, but WSL may not have dbus installed, or may not have it set up as expected. As such, you may see the following error:

`secretstorage.exceptions.SecretServiceNotAvailableException: Environment variable DBUS_SESSION_BUS_ADDRESS is unset`

Please either check your WSL dbus installation/configuration, or run the script nativity on windows.

### "Unable to get key for cookie decryption" error, especially in Chrome

There is currently an issue with [browser_cookie3](https://github.com/borisbabic/browser_cookie3). This has been reported within this repo [here](https://github.com/easlice/bandcamp-downloader/issues/17) and you can see the status of it upstream [here](https://github.com/borisbabic/browser_cookie3/issues/141).

### "Failed to find <browser> cookie" even though you have the browser installed and are logged in.

Sometimes a browser does not put its files in the expected location. This is especially true if the browser is installed as a flatpack or snap. As such, browser_cookie3 doesn't know where to look for the cookie store.

You can fix this by using the `--cookies` flag and giving it the path to your browser's cookie store, usually a file named something like `Cookies` or `cookies.sqlite`. Note: You still need to give the correct `--browser` flag.

Another option is to symlink the directory to the correct place. For example, the package `chromium-bin` often installs to the directory `~/.chromium-bin` but it is expected to be at `~.chromium`. You can run:
`symlink -s ~/.chromium-bin ~/.chromium`
and then browser_cookie3 will be able to find the cookies as expected and you will not need to use the `--cookies` flag.

## Manual Setup

Install the script dependencies by running:

```
pip install .
```

Run the program:

```
bandcamp-downloader.py [arguments]
```

If you run into errors or dependency issues, you can try installing exact dependency versions by running:

```
pip install -r requirements.txt
```

## Setup via Poetry

Install requirements using [Python Poetry](https://python-poetry.org/). [Installation instructions here](https://python-poetry.org/docs/#installation).

```
poetry install
```

Run the script within the poetry shell:

```
poetry shell
python bandcamp-downloader.py [arguments]
```

or directly through `poetry run`:

```
poetry run python bandcamp-downloader.py [arguments]
```

## Usage
```
usage: bandcamp-downloader.py [-h]
                              [--browser {firefox,chrome,chromium,brave,opera,edge}]
                              [--cookies COOKIES]
                              [--directory DIRECTORY]
                              [--filename-format FILENAME_FORMAT]
                              [--format {aac-hi,aiff-lossless,alac,flac,mp3-320,mp3-v0,vorbis,wav}]
                              [--parallel-downloads PARALLEL_DOWNLOADS]
                              [--force]
                              [--wait-after-download WAIT_AFTER_DOWNLOAD]
                              [--max-download-attempts MAX_DOWNLOAD_ATTEMPTS]
                              [--retry-wait RETRY_WAIT]
                              [--include-hidden]
                              [--download-since DOWNLOAD_SINCE]
                              [--dry-run]
                              [--extract]
                              [--verbose] [-v]
                              username

Download your collection from bandcamp. Requires a logged in session in a
supported browser so that the browser cookies can be used to authenticate with
bandcamp. Albums are saved into directories named after their artist. Already
existing albums will have their file size compared to what is expected and re-
downloaded if the sizes differ. Otherwise already existing albums will not be
re-downloaded.

positional arguments:
  username              Your bandcamp username, as it appears at the end of
                        your bandcamp collection url, I.E. bandcamp.com/user_name

optional arguments:
  -h, --help            show this help message and exit
  --browser {firefox,chrome,chromium,brave,opera,edge}, -b {firefox,chrome,chromium,brave,opera,edge}
                        The browser whose cookies to use for accessing
                        bandcamp. Defaults to "firefox"
  --cookies COOKIES, -c COOKIES
                        Path to a cookie file. First, we will try to use
                        it as a mozilla cookie jar. If that fails, it'll
                        be used as the path for your given browser's cookie store.
  --directory DIRECTORY, -d DIRECTORY
                        The directory to download albums to. Defaults to the
                        current directory.
  --filename-format FILENAME_FORMAT
                        The filename format for downloaded tracks. Default is
                        '{artist}/{artist} - {title}'.
                        All placeholders: item_id, artist, title
  --format {aac-hi,aiff-lossless,alac,flac,mp3-320,mp3-v0,vorbis,wav}, -f {aac-hi,aiff-lossless,alac,flac,mp3-320,mp3-v0,vorbis,wav}
                        What format to download the songs in. Default is
                        'mp3-320'.
  --parallel-downloads PARALLEL_DOWNLOADS, -p PARALLEL_DOWNLOADS
                        How many threads to use for parallel downloads. Set to
                        '1' to disable parallelism. Default is 5. Must be
                        between 1 and 32
  --force               Always re-download existing albums, even if they
                        already exist.
  --wait-after-download WAIT_AFTER_DOWNLOAD
                        How long, in seconds, to wait after successfully
                        completing a download before downloading the next
                        file. Defaults to '1'.
  --max-download-attempts MAX_DOWNLOAD_ATTEMPTS
                        How many times to try downloading any individual files
                        before giving up on it. Defaults to '5'.
  --retry-wait RETRY_WAIT
                        How long, in seconds, to wait before trying to
                        download a file again after a failure. Defaults to
                        '5'.
  --include-hidden      Download items in your collection that have been marked as hidden.
  --download-since DOWNLOAD_SINCE
                        Only download items purchased on or after the given date.
                        YYYY-MM-DD format, defaults to all items.
  --dry-run             Don't actually download files, just process all the web data
                        and report what would have been done.
                        
  --extract               Unzip all albums into a subfolder named after the album, under the artist folder.
                        Deletes the zip file on completion of command.  
  --verbose, -v
```

## Development and Contributing

When modifying required packages, please:

* Add to Poetry (`poetry add`)
* Then update the `requirements.txt` (`poetry run pip freeze > requirements.txt`) and the dependencies in `setup.py`.
* Commit all updated files

## Notes

If you have a logged in session in the browser, have used the `--browser`/`-b` flag correctly, and still are being told that the script isn't finding any albums, check out the page for [browser_cookie3](https://github.com/borisbabic/browser_cookie3), you might need to do some configuring in your browser to make the cookies available to the script.

If you are downloading your collection in multiple formats, the script can't tell if an already downloaded zip file is the same format or not, and will happily overwrite it. So make sure to use different directories for different formats, either by running the script somewhere else or by supplying directories to the `--directory`/`-d` flag.

If you are running windows and having issues getting things running (that is not related to WSL crashes, DBUS errors, or Visual C++ errors) you might have some luck with some of the information in [this issue report](https://github.com/easlice/bandcamp-downloader/issues/21).

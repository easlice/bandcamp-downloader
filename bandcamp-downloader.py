#!/usr/bin/env python3

import argparse
import datetime
import glob
import html
import http
import json
import os
import re
import sys
import time
import urllib.parse
import traceback
import zipfile

from concurrent.futures import ThreadPoolExecutor

# These require pip installs
from bs4 import BeautifulSoup, SoupStrainer
from curl_cffi import requests
import browser_cookie3
from tqdm import tqdm

USER_URL = 'https://bandcamp.com/{}'
COLLECTION_POST_URL = 'https://bandcamp.com/api/fancollection/1/collection_items'
HIDDEN_POST_URL = 'https://bandcamp.com/api/fancollection/1/hidden_items'
FILENAME_REGEX = re.compile('filename\\*=UTF-8\'\'(.*)')
SANITIZE_PATH_WINDOWS_REGEX = re.compile(r'[<>:"/|?*\\]')
CONFIG = {
    'VERBOSE' : False,
    'OUTPUT_DIR' : None,
    'BROWSER' : None,
    'FORMAT' : None,
    'FORCE' : False,
    'TQDM' : None,
    'MAX_URL_ATTEMPTS' : 5,
    'URL_RETRY_WAIT' : 5,
    'POST_DOWNLOAD_WAIT' : 1,
    'SINCE' : None,
}
MAX_THREADS = 32
DEFAULT_THREADS = 5
DEFAULT_FILENAME_FORMAT = os.path.join('{artist}', '{artist} - {title}')
FORMAT_EXTENSIONS = {
    'aac-hi': '.m4a',
    'aiff-lossless': '.aiff',
    'alac': '.m4a',
    'flac': '.flac',
    'mp3-320': '.mp3',
    'mp3-v0': '.mp3',
    'vorbis': '.ogg',
    'wav': '.wav'
}
SUPPORTED_BROWSERS = [
    'firefox',
    'chrome',
    'chromium',
    'brave',
    'opera',
    'edge'
]
TRACK_INFO_KEYS = [
    'item_id',
    'artist',
    'title'
]

def main() -> int:
    parser = argparse.ArgumentParser(description = 'Download your collection from bandcamp. Requires a logged in session in a supported browser so that the browser cookies can be used to authenticate with bandcamp. Albums are saved into directories named after their artist. Already existing albums will have their file size compared to what is expected and re-downloaded if the sizes differ. Otherwise already existing albums will not be re-downloaded.')
    parser.add_argument('username', type=str, help='Your bandcamp username, as it appears at the end of your bandcamp collection url, I.E. bandcamp.com/user_name')
    parser.add_argument(
        '--browser', '-b',
        type=str,
        default = 'firefox',
        choices = SUPPORTED_BROWSERS,
        help='The browser whose cookies to use for accessing bandcamp. Defaults to "firefox"'
    )
    parser.add_argument(
        '--cookies', '-c',
        type=str,
        help='Path to a cookie file. First, we will try to use it as a mozilla cookie jar. If that fails, it\'ll be used as the path for your given browser\'s cookie store.',
    )
    parser.add_argument(
        '--directory', '-d',
        default = os.getcwd(),
        help='The directory to download albums to. Defaults to the current directory.'
    )
    parser.add_argument(
        '--filename-format',
        default = DEFAULT_FILENAME_FORMAT,
        help='The filename format for downloaded tracks. Default is \'{}\'. All placeholders: {}'.format(DEFAULT_FILENAME_FORMAT, ', '.join(TRACK_INFO_KEYS))
    )
    parser.add_argument(
        '--format', '-f',
        default = 'mp3-320',
        choices = FORMAT_EXTENSIONS.keys(),
        help = 'What format to download the songs in. Default is \'mp3-320\'.'
    )
    parser.add_argument(
        '--parallel-downloads', '-p',
        type = int,
        default = DEFAULT_THREADS,
        help = 'How many threads to use for parallel downloads. Set to \'1\' to disable parallelism. Default is 5. Must be between 1 and {}'.format(MAX_THREADS),
    )
    parser.add_argument(
        '--force',
        action = 'store_true',
        default = False,
        help = 'Always re-download existing albums, even if they already exist.',
    )
    parser.add_argument(
        '--wait-after-download',
        type = float,
        default = 1,
        help = 'How long, in seconds, to wait after successfully completing a download before downloading the next file. Defaults to \'1\'.',
    )
    parser.add_argument(
        '--max-download-attempts',
        type = int,
        default = 5,
        help = 'How many times to try downloading any individual files before giving up on it. Defaults to \'5\'.',
    )
    parser.add_argument(
        '--retry-wait',
        type = float,
        default = 5,
        help = 'How long, in seconds, to wait before trying to download a file again after a failure. Defaults to \'5\'.',
    )
    parser.add_argument(
        '--include-hidden',
        action='store_true',
        default=False,
        help = 'Download items in your collection that have been marked as hidden.',
    )
    parser.add_argument(
        '--download-since',
        default = '',
        help = 'Only download items purchased on or after the given date. YYYY-MM-DD format, defaults to all items.'
    )
    parser.add_argument(
        '--extract', '-x',
        action='store_true',
        help='Extracts downloaded albums, organised in {ARTIST}/{ALBUM} subdirectories. Songs are extracted to the '
             'path specified in the `--directory`/`-d` flag, otherwise to the current directory if not specified. '
             'Upon completion, original .zip file is deleted.'
    )
    parser.add_argument(
        '--dry-run',
        action = 'store_true',
        default = False,
        help = 'Don\'t actually download files, just process all the web data and report what would have been done.',
    )
    parser.add_argument('--verbose', '-v', action='count', default = 0)
    args = parser.parse_args()

    if args.parallel_downloads < 1 or args.parallel_downloads > MAX_THREADS:
        parser.error('--parallel-downloads must be between 1 and 32.')

    CONFIG['COOKIES'] = args.cookies
    CONFIG['VERBOSE'] = args.verbose
    CONFIG['OUTPUT_DIR'] = os.path.normcase(args.directory)
    CONFIG['FILENAME_FORMAT'] = args.filename_format
    CONFIG['BROWSER'] = args.browser
    if args.download_since:
        CONFIG['SINCE'] = datetime.datetime.strptime(args.download_since, '%Y-%m-%d')
    CONFIG['FORMAT'] = args.format
    CONFIG['FORCE'] = args.force
    CONFIG['DRY_RUN'] = args.dry_run
    CONFIG['EXTRACT'] = args.extract

    if args.wait_after_download < 0:
        parser.error('--wait-after-download must be at least 0.')
    if args.max_download_attempts < 1:
        parser.error('--max-download-attempts  must be at least 1.')
    if args.retry_wait < 0:
        parser.error('--retry-wait must be at least 0.')
    CONFIG['POST_DOWNLOAD_WAIT'] = args.wait_after_download
    CONFIG['MAX_URL_ATTEMPTS'] = args.max_download_attempts
    CONFIG['URL_RETRY_WAIT'] = args.retry_wait

    if CONFIG['VERBOSE']: print(args)
    if CONFIG['FORCE']: print('WARNING: --force flag set, existing files will be overwritten.')
    CONFIG['COOKIE_JAR'] = get_cookies()

    items = get_items_for_user(args.username, args.include_hidden)
    if not items:
        print('WARN: No album links found for user [{}]. Are you logged in and have you selected the correct browser to pull cookies from?'.format(args.username))
        sys.exit(2)
    if CONFIG['VERBOSE']: print('Found [{}] downloadable items in [{}]\'s collection.'.format(len(items), args.username))

    if CONFIG['SINCE']:
        # Filter items by purchase time
        items = {key: items[key] for key in items
                 if purchase_time_ok(items[key], CONFIG['SINCE'])}
        if not items:
            print('No album links purchased since [{}].'.format(CONFIG['SINCE']))
            sys.exit(0)

        if CONFIG['VERBOSE']:
            print('[{}] album links purchased since [{}].'.format(len(items), CONFIG['SINCE']))

    print('Starting album downloads...')
    CONFIG['TQDM'] = tqdm(items, unit = 'album')
    if args.parallel_downloads > 1:
        with ThreadPoolExecutor(max_workers = args.parallel_downloads) as executor:
            for item in items.values(): executor.submit(download_and_log_album, item)
    else:
        for album in items.values():
            download_and_log_album(album)
    CONFIG['TQDM'].close()

    downloaded_zips = [item['file_path'] + '.zip' for item in items.values() if item['extension'] == '.zip' and item['downloaded']]
    print(downloaded_zips)
    if args.extract:
        for zip in downloaded_zips:
            print(f'Extracting compressed archive: {zip}')
            if CONFIG['DRY_RUN']: continue
            album_name = re.search(r'\- (.+?)\.zip$', zip).group(1)
            extract_dir = os.path.join(os.path.dirname(zip), album_name)
            with zipfile.ZipFile(zip, 'r') as zip_file:
                zip_file.extractall(extract_dir)
            os.remove(zip)

    print('Done.')

    return 0

# Fetch item data for the given user via the bandcamp API, then return the
# 'items' subobject, with 'redownload_url' and 'filename' fields added to each.
def fetch_items(_hidden : bool, _user_id : str, _last_token : str, _count : int) -> dict:
    url = COLLECTION_POST_URL if not _hidden else HIDDEN_POST_URL
    if _count <= 0: return {}
    payload = {
        'fan_id' : _user_id,
        'count' : _count,
        'older_than_token' : _last_token,
    }
    response = requests.post(
        url,
        data = json.dumps(payload),
        cookies = CONFIG['COOKIE_JAR'],
        impersonate='chrome'
    )
    response.raise_for_status()
    data = json.loads(response.text)

    return merge_items_and_urls(data['items'], data['redownload_urls'] or {})

# Loads the given url and looks for the 'pagedata' div and, if found,
# returns its 'data-blob' property decoded from json.
def pagedata_for_url(_url : str) -> dict:
    text = requests.get(_url, cookies = CONFIG['COOKIE_JAR']).text
    soup = BeautifulSoup(
        text,
        'html.parser',
        parse_only = SoupStrainer('div', id='pagedata'),
    )
    div = soup.find('div')
    if not div: return {}
    return json.loads(html.unescape(div.get('data-blob')))

# Returns a dictionary mapping item key to item. In addition to the basic
# bandcamp API item dict, returned items have their redownload url in
# field 'redownload_url' and their download path (excluding extension) in
# 'file_path'.
def get_items_for_user(_user : str, _include_hidden : bool) -> dict:
    # Get the initial metadata from the json in the 'data-blob' div on the
    # user landing page.
    user_url = USER_URL.format(_user)
    data = pagedata_for_url(user_url)
    if not data:
        print('ERROR: No data found at user url [{}]'.format(user_url))
        exit(2)
    if 'collection_count' not in data:
        print('ERROR: No collection info for user {}.\nPlease double check that your given username is correct.\nIt should be given exactly as it appears at the end of your bandcamp user url.\nFor example: bandcamp.com/user_name'.format(
            _user
        ))
        exit(2)
    user_id = data['fan_data']['fan_id']

    items = data['item_cache']['collection']
    if _include_hidden:
        items.update(data['item_cache']['hidden'])
    # Attach download urls to the first page of items.
    items = merge_items_and_urls(items.values(), data['collection_data']['redownload_urls'])

    # Fetch the rest of the visible library items.
    items.update(fetch_items(
        False,
        user_id,
        data['collection_data']['last_token'],
        # count is the number we have left to fetch after the initial data blob
        data['collection_data']['item_count'] - len(data['item_cache']['collection'])))

    if _include_hidden:
        # Fetch the rest of the hidden library items.
        items.update(fetch_items(
            True,
            user_id,
            data['hidden_data']['last_token'],
            data['hidden_data']['item_count'] - len(data['item_cache']['hidden'])))

    # Calculate filenames and handle collisions
    add_item_file_paths(items)

    return items

# Returns true if the item's purchase time is no earlier than the given
# cutoff, or if the item's purchase time can't be found.
def purchase_time_ok(_item : dict, _since : datetime.datetime) -> bool:
    # If there's no purchased field we have to say yes since we can't
    # reliably exclude this item.
    if 'purchased' not in _item: return True

    # If there is a purchased field, compare it to the given cutoff.
    purchaseTime = datetime.datetime.strptime(_item['purchased'], '%d %b %Y %H:%M:%S GMT')
    return purchaseTime >= _since

# Returns true if a valid item key can be assembled from the given item dict.
def item_has_key(_item) -> bool:
    return 'sale_item_type' in _item and 'sale_item_id' in _item

# Returns the canonical key for an item (within the bandcamp API),
# its type followed by its item id.
def key_for_item(_item) -> str:
    return str(_item['sale_item_type']) + str(_item['sale_item_id'])

# Merges the 'redownload_urls' dict into the 'items' dict from the bandcamp
# item list. The result is a dict keyed by key_for_item(item), where each
# item has the added key 'redownload_url'.
# Items with mismatched keys or no redownload url are dropped.
def merge_items_and_urls(_items : list, _urls : dict) -> dict:
    results = {}
    for item in _items:
        if not item_has_key(item) or key_for_item(item) not in _urls:
            print("WARN: couldn't find redownload URL for item_id:[{}], artist:[{}], title:[{}]".format(
                item['item_id'], item['band_name'], item['item_title']))
            continue
        key = key_for_item(item)
        new_item = dict(item)
        new_item['redownload_url'] = _urls[key]
        results[key] = new_item
    return results

# Given a dictionary of bandcamp items, calculates their download path based
# on the configured format, appends the item key if two names collide, and
# stores the result in the item key 'file_path'.
def add_item_file_paths(_items : dict):
    filenames = {} # maps item key to the computed filename
    filename_counts = {} # number of occurrences of each computed filename
    # First calculate the user-formatted name for everything
    for key,item in _items.items():
        # For backwards compatibility, use 'artist' and 'title' (the equivalent
        # keys from the album landing page data blob) instead of 'band_name'
        # and 'item_title' when parsing the filename format.
        track_info = {
            'item_id': item['item_id'],
            'artist': sanitize_value(item['band_name']),
            'title': sanitize_value(item['item_title']),
        }
        filename = CONFIG['FILENAME_FORMAT'].format(**track_info)
        filenames[key] = filename
        filename_counts[filename] = filename_counts.get(filename, 0) + 1

    # Now rescan to apply final (deduped) paths.
    for key,item in _items.items():
        filename = filenames[key]
        if filename_counts[filename] > 1:
            # This filename is not unique, append the (globally unique) item
            # key so their downloads don't overwrite each other.
            filename = filename + '-' + key
        item['file_path'] = os.path.join(CONFIG['OUTPUT_DIR'], filename)

# Check if a file already exists at the given path that matches the given
# metadata size string.
# _download_size, if nonempty, should be of the form "[num]MB" or "[num]GB"
def download_exists(_file_path : str, _download_size : str) -> bool:
    if not os.path.exists(_file_path):
        return False
    if CONFIG['FORCE']:
        if CONFIG['VERBOSE']: CONFIG['TQDM'].write('--force flag was given. Overwriting existing file at [{}].'.format(_file_path))
        return False
    if not _download_size:
        # This is rare but can happen, a few downloads have no size
        # metadata -- to be safe, don't report that we already have
        # the file if we can't verify the size.
        if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('File at [{}] has no expected download size. Re-downloading.'.format(_file_path))
        return False

    actual_size = os.stat(_file_path).st_size
    if _download_size.endswith("MB"):
        actual_mb = actual_size / (1024 * 1024)
        expected_mb = float(_download_size[:-2])
        offset = abs(actual_mb - expected_mb)
    elif _download_size.endswith("GB"):
        # the field is called "size_mb" but it also uses GB
        actual_gb = actual_size / (1024 * 1024 * 1024)
        expected_gb = float(_download_size[:-2])
        offset = abs(actual_gb - expected_gb)
    else:
        if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('File at [{}] has unrecognized expected download size [{}]. Re-downloading.'.format(_file_path, _download_size))
        return False
    # We should expect a difference of <= 0.05 since bandcamp always gives
    # sizes to one decimal place, but sometimes the reported value is slightly
    # off. The main cause seems to be that archives are regenerated when
    # needed and compression settings are not stable over time.
    if offset < 0.15:
        if CONFIG['VERBOSE'] >= 3: CONFIG['TQDM'].write('Skipping file that already exists: [{}]'.format(_file_path))
        return True
    if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('File at [{}] is the wrong size. Expected [{}] but was [{}]. Re-downloading.'.format(_file_path, _download_size, actual_size))
    return False

# Get the pagedata json for a url, applying retry / wait settings.
def pagedata_with_retry(_url : str) -> dict:
    for attempt in range(CONFIG['MAX_URL_ATTEMPTS']):
        # If this isn't the first attempt, apply the retry wait interval.
        if attempt != 0: time.sleep(CONFIG['URL_RETRY_WAIT'])
        try:
            data = pagedata_for_url(_url)
            if data: return data
            if CONFIG['VERBOSE']: CONFIG['TQDM'].write('WARN: no pagedata found fetching album url [{}] (you may be rate-limited, try increasing --wait-after-download or --retry-wait)'.format(_url))
        except IOError as e:
            if CONFIG['VERBOSE'] >=2: CONFIG['TQDM'].write('WARN: I/O Error on attempt # [{}] to download the page data at [{}].'.format(attempt, _url))
        except Exception as e:
            print_exception(e, 'An exception occurred trying to download album url [{}]:'.format(_url))
    CONFIG['TQDM'].write("ERROR: Couldn't download pagedata for album at url [{}]".format(_url))
    return {}

# Download the file for the given bandcamp album, and notify TQDM when done.
def download_and_log_album(_album : dict):
    try:
        download_album(_album)
    except Exception as e:
         band_name = _album.get('band_name', '')
         title = _album.get('item_title', '')
         print_exception(e, 'Trying to download album [{}] with artist [{}] and title [{}]:'.format(
             key_for_item(_album), band_name, title))
    CONFIG['TQDM'].update()
    time.sleep(CONFIG['POST_DOWNLOAD_WAIT'])

# Download the file for the given bandcamp album. Sets the key 'extension'
# to the file extension for this item, and the key 'downloaded' to whether
# a file was successfully downloaded.
def download_album(_album : dict):
    _album['downloaded'] = False
    if 'tralbum_type' in _album:
        _album['extension'] = extension_from_type(_album['tralbum_type'], CONFIG['FORMAT'])
    else:
        # This key should never be missing, but if it is, we'll try to recover
        # the extension later from the download itself.
        _album['extension'] = ''
    album_url = _album['redownload_url']
    data = pagedata_with_retry(album_url)
    if not data: return # pagedata_with_retry already logged failure
    download_item = data['download_items'][0]
    title = download_item['title']

    if not 'downloads' in download_item:
        CONFIG['TQDM'].write('WARN: Album [{}] at url [{}] has no downloads available.'.format(title, album_url))
        return

    if not CONFIG['FORMAT'] in download_item['downloads']:
        CONFIG['TQDM'].write('WARN: Album [{}] at url [{}] does not have a download for format [{}].'.format(title, album_url, CONFIG['FORMAT']))
        return

    download = download_item['downloads'][CONFIG['FORMAT']]
    download_url = download['url']
    download_size = download.get('size_mb', None)
    # If this is an unknown format, get the extension from the download url.
    if not _album['extension']: _album['extension'] = extension_from_url(download_url)

    file_path = _album['file_path'] + _album['extension']
    # Only start the download if a matching file doesn't already exist.
    if not download_exists(file_path, download_size):
        _album['downloaded'] = download_file(download_url, _album)

def download_file(_url : str, _album : dict, _attempt : int = 1) -> bool:
    """Download the given url to the given file path.

    Returns True if the url was successfully downloaded, False otherwise."""
    if CONFIG['DRY_RUN']:
        if CONFIG['VERBOSE'] >= 2:
            CONFIG['TQDM'].write('Dry run: skipping file download for url [{}]'.format(_url))
        return True
    try:
        response = requests.get(
                _url,
                cookies = CONFIG['COOKIE_JAR'],
                impersonate='chrome',
                stream = True,
        )
        response.raise_for_status()

        expected_size = int(response.headers['content-length'])
        filename_match = FILENAME_REGEX.search(response.headers['content-disposition'])
        original_filename = urllib.parse.unquote(filename_match.group(1)) if filename_match else _url.split('/')[-1]
        extension = os.path.splitext(original_filename)[1]
        if extension != '' and extension != _album['extension']:
            if CONFIG['VERBOSE']: CONFIG['TQDM'].write('WARN: expected extension [{}] but download at [{}] gives [{}].'.format(
                _album['extension'], _url, extension))
            _album['extension'] = extension
        file_path = _album['file_path'] + _album['extension']
        if os.path.exists(file_path) and not CONFIG['FORCE']:
            # This should be quite rare since we already screened existing
            # files against the album's size metadata, but check one last
            # time if we already have a file of the right size.
            actual_size = os.stat(file_path).st_size
            if expected_size == actual_size:
                if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('Canceling download that matches existing file: [{}]'.format(file_path))
                return False

        if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('Album being saved to [{}]'.format(file_path))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'wb') as fh:
            for chunk in response.iter_content():
                fh.write(chunk)
            actual_size = fh.tell()
        if expected_size != actual_size:
            raise IOError('Incomplete read. {} bytes read, {} bytes expected'.format(actual_size, expected_size))
        return True
    except IOError as e:
        if _attempt < CONFIG['MAX_URL_ATTEMPTS']:
            if CONFIG['VERBOSE'] >=2: CONFIG['TQDM'].write('WARN: I/O Error on attempt # [{}] to download the file at [{}]. Trying again...'.format(_attempt, _url))
            time.sleep(CONFIG['URL_RETRY_WAIT'])
            return download_file(_url, _album, _attempt + 1)
        else:
            print_exception(e, 'An exception occurred trying to download file url [{}]:'.format(_url))
    except Exception as e:
        print_exception(e, 'An exception occurred trying to download file url [{}]:'.format(_url))
    return False

def print_exception(_e : Exception, _msg : str = '') -> None:
    CONFIG['TQDM'].write('\nERROR: {}'.format(_msg))
    CONFIG['TQDM'].write('\n'.join(traceback.format_exception(etype=type(_e), value=_e , tb=_e.__traceback__)))
    CONFIG['TQDM'].write('\n')

# Windows has some picky requirements about file names
# So let's replace known bad characters with '-'
def sanitize_filename(_path : str) -> str:
    if sys.platform.startswith('win'):
        return re.sub(SANITIZE_PATH_WINDOWS_REGEX, '-', _path)
    else:
        # Remove `/`
        return _path.replace('/', '-')

def sanitize_value(_value : any) -> any:
    if type(_value) == str: return sanitize_filename(_value)
    return _value

def extension_from_url(_url : str) -> str:
    path = urllib.parse.urlparse(_url).path

    filename = path.split('/')[-1]
    if '.' not in filename: return ''
    return path[path.rindex('.'):]

def extension_from_type(_download_type : str, _format : str) -> str:
    if _download_type == "a": return ".zip"
    if CONFIG['FORMAT'] in FORMAT_EXTENSIONS:
        return FORMAT_EXTENSIONS[CONFIG['FORMAT']]
    return ''

def get_cookies():
    if CONFIG['COOKIES']:
        # First try it as a mozilla cookie jar
        try:
            cj = http.cookiejar.MozillaCookieJar(CONFIG['COOKIES'])
            cj.load()
            return cj
        except Exception as e:
            if CONFIG['VERBOSE'] >=2: print(f"Cookie file at [{CONFIG['COOKIES']}] not a mozilla cookie jar.\nTrying it as a cookie store for the browser [{CONFIG['BROWSER']}]...")
        # Next try it with browser_cookie
        try:
            func = getattr(browser_cookie3, CONFIG['BROWSER'])
            return func(domain_name = 'bandcamp.com', cookie_file = CONFIG['COOKIES'])
        except AttributeError:
            raise Exception('Browser type [{}] is unknown. Can\'t pull cookies, so can\'t authenticate with bandcamp.'.format(CONFIG['BROWSER']))
    try:
        func = getattr(browser_cookie3, CONFIG['BROWSER'])
        return func(domain_name = 'bandcamp.com')
    except AttributeError:
        raise Exception('Browser type [{}] is unknown. Can\'t pull cookies, so can\'t authenticate with bandcamp.'.format(CONFIG['BROWSER']))

def _is_zip(file_path: str) -> bool:
    # Determine if the file is a compressed .zip archive
    return file_path.endswith('.zip') if file_path else False


if __name__ == '__main__':
    sys.exit(main())

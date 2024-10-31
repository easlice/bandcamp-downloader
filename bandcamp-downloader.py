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
import requests
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
SUPPORTED_FILE_FORMATS = [
    'aac-hi',
    'aiff-lossless',
    'alac',
    'flac',
    'mp3-320',
    'mp3-v0',
    'vorbis',
    'wav',
]
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
        choices = SUPPORTED_FILE_FORMATS,
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
    if CONFIG['VERBOSE']: print('Found [{}] links for [{}]\'s collection.'.format(len(items), args.username))

    if CONFIG['SINCE']:
        # Filter items by purchase time
        items = {key: items[key] for key in items
                 if purchase_time_ok(items[key], CONFIG['SINCE'])}
        if not items:
            print('No album links purchased since [{}].'.format(CONFIG['SINCE']))
            sys.exit(0)

        if CONFIG['VERBOSE']:
            print('[{}] album links purchased since [{}].'.format(len(items), CONFIG['SINCE']))


    links = [item['redownload_url'] for item in items.values()]

    print('Starting album downloads...')
    downloaded_zips = []
    CONFIG['TQDM'] = tqdm(links, unit = 'album')
    if args.parallel_downloads > 1:
        with ThreadPoolExecutor(max_workers = args.parallel_downloads) as executor:
            downloaded_zips = [file_path for file_path in list(executor.map(download_album, links)) if _is_zip(file_path)]
    else:
        for link in links:
            file_path = download_album(link)
            if _is_zip(file_path):
                downloaded_zips.append(file_path)
    CONFIG['TQDM'].close()
    print(downloaded_zips)
    if args.extract:
        for zip in downloaded_zips:
            print(f'Extracting compressed archive: {zip}')
            album_name = re.search(r'\- (.+?)\.zip$', zip).group(1)
            extract_dir = os.path.join(os.path.dirname(zip), album_name)
            with zipfile.ZipFile(zip, 'r') as zip_file:
                zip_file.extractall(extract_dir)
            os.remove(zip)

    print('Done.')

    return 0

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
def merge_items_and_urls(_items : dict, _urls : dict) -> dict:
    results = {}
    for key,item in _items:
        if not item_has_key(item) or key_for_item(item) != key or key not in _urls:
            continue
        new_item = dict(item)
        new_item['redownload_url'] = _urls[key]
        # Just in case the "canonical" key doesn't match the original dictionary,
        # recalculate it when adding to the results.
        results[key_for_item(new_item)] = new_item
    return results

# Fetch item data for the given user via the bandcamp API, then return the
# 'items' subobject, with a 'redownload_url' field added to each.
def bandcamp_fetch_items(_hidden : bool, _user_id : str, _last_token : str, _count : int) -> dict:
    url = COLLECTION_POST_URL if not _hidden else HIDDEN_POST_URL
    if _count <= 0: return {}
    payload = {
        'fan_id' : _user_id,
        'count' : _count,
        'older_than_token' : _last_token,
    }
    with requests.post(
        url,
        data = json.dumps(payload),
        cookies = CONFIG['COOKIE_JAR'],
    ) as response:
        response.raise_for_status()
        data = json.loads(response.text)

        return merge_items_and_urls(data['items'], data['redownload_urls'] or {})

# Loads the given url and looks for the 'data-blob' property on the div
# inside 'pagedata'. If found, converts it from json to a dict.
# Used to fetch the initial user metadata from their account landing page.
def data_blob_for_url(_url : str) -> dict:
    soup = BeautifulSoup(
        requests.get(_url, cookies = CONFIG['COOKIE_JAR']).text,
        'html.parser',
        parse_only = SoupStrainer('div', id='pagedata'),
    )
    div = soup.find('div')
    if not div:
        print('ERROR: No div with pagedata found for user at url [{}]'.format(_url))
        return {}
    data = json.loads(html.unescape(div.get('data-blob')))
    return data

# Returns a dictionary mapping item key to item. In addition to the basic
# bandcamp API item dict, returned items have their redownload url in
# field 'redownload_url'.
def get_items_for_user(_user : str, _include_hidden : bool) -> dict:
    # Get the initial metadata from the json in the 'data-blob' div on the
    # user landing page.
    data = data_blob_for_url(USER_URL.format(_user))
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
    items = merge_items_and_urls(items, data['collection_data']['redownload_urls'])
    
    # Fetch the rest of the visible library items.
    items.update(bandcamp_fetch_items(
        False,
        user_id,
        data['collection_data']['last_token'],
        # count is the number we have left to fetch after the initial data blob
        data['collection_data']['item_count'] - len(data['item_cache']['collection'])))

    if _include_hidden:
        # Fetch the rest of the hidden library items.
        items.update(bandcamp_fetch_items(
            True,
            user_id,
            data['hidden_data']['last_token'],
            data['hidden_data']['item_count'] - len(data['item_cache']['hidden'])))
    
    return items

def download_album(_album_url : str, _attempt : int = 1) -> str:
    try:
        soup = BeautifulSoup(
            requests.get(
                _album_url,
                cookies = CONFIG['COOKIE_JAR']
            ).text,
            'html.parser',
            parse_only = SoupStrainer('div', id='pagedata'),
        )
        div = soup.find('div')
        if not div:
            CONFIG['TQDM'].write('ERROR: No div with pagedata found for album at url [{}]'.format(_album_url))
            return

        data = json.loads(html.unescape(div.get('data-blob')))
        album = data['download_items'][0]['title']

        if not 'downloads' in data['download_items'][0]:
            CONFIG['TQDM'].write('WARN: Album [{}] at url [{}] has no downloads available.'.format(album, _album_url))
            return

        if not CONFIG['FORMAT'] in data['download_items'][0]['downloads']:
            CONFIG['TQDM'].write('WARN: Album [{}] at url [{}] does not have a download for format [{}].'.format(album, _album_url, CONFIG['FORMAT']))
            return

        download_url = data['download_items'][0]['downloads'][CONFIG['FORMAT']]['url']
        track_info = {key: data['download_items'][0][key] for key in TRACK_INFO_KEYS}
        return download_file(download_url, track_info)
    except IOError as e:
        if _attempt < CONFIG['MAX_URL_ATTEMPTS']:
            if CONFIG['VERBOSE'] >=2: CONFIG['TQDM'].write('WARN: I/O Error on attempt # [{}] to download the album at [{}]. Trying again...'.format(_attempt, _album_url))
            time.sleep(CONFIG['URL_RETRY_WAIT'])
            download_album(_album_url, _attempt + 1)
        else:
            print_exception(e, 'An exception occurred trying to download album url [{}]:'.format(_album_url))
    except Exception as e:
        print_exception(e, 'An exception occurred trying to download album url [{}]:'.format(_album_url))
    finally:
        # only tell TQDM we're done on the first call
        if _attempt == 1:
            CONFIG['TQDM'].update()
            time.sleep(CONFIG['POST_DOWNLOAD_WAIT'])

def download_file(_url : str, _track_info : dict = None, _attempt : int = 1) -> str:
    """Return a string representing the absolute path to the file being downloaded"""
    try:
        with requests.get(
                _url,
                cookies = CONFIG['COOKIE_JAR'],
                stream = True,
        ) as response:
            response.raise_for_status()

            expected_size = int(response.headers['content-length'])
            filename_match = FILENAME_REGEX.search(response.headers['content-disposition'])
            original_filename = urllib.parse.unquote(filename_match.group(1)) if filename_match else _url.split('/')[-1]
            extension = os.path.splitext(original_filename)[1]
            # Sanitize all input values for formatting
            safe_track_info = {
                key: (sanitize_filename(value) if type(value) == str else value) for key, value in _track_info.items()
            } if _track_info else {}
            filename = CONFIG['FILENAME_FORMAT'].format(**safe_track_info) + extension
            file_path = os.path.join(CONFIG['OUTPUT_DIR'], filename)
            if os.path.exists(file_path):
                if CONFIG['FORCE']:
                    if CONFIG['VERBOSE']: CONFIG['TQDM'].write('--force flag was given. Overwriting existing file at [{}].'.format(file_path))
                else:
                    actual_size = os.stat(file_path).st_size
                    if expected_size == actual_size:
                        if CONFIG['VERBOSE'] >= 3: CONFIG['TQDM'].write('Skipping album that already exists: [{}]'.format(file_path))
                        return file_path
                    else:
                        if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('Album at [{}] is the wrong size. Expected [{}] but was [{}]. Re-downloading.'.format(file_path, expected_size, actual_size))

            if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('Album being saved to [{}]'.format(file_path))
            if CONFIG['DRY_RUN']:
                return file_path
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'wb') as fh:
                for chunk in response.iter_content(chunk_size=8192):
                    fh.write(chunk)
                actual_size = fh.tell()
            if expected_size != actual_size:
                raise IOError('Incomplete read. {} bytes read, {} bytes expected'.format(actual_size, expected_size))
    except IOError as e:
        if _attempt < CONFIG['MAX_URL_ATTEMPTS']:
            if CONFIG['VERBOSE'] >=2: CONFIG['TQDM'].write('WARN: I/O Error on attempt # [{}] to download the file at [{}]. Trying again...'.format(_attempt, _url))
            time.sleep(CONFIG['URL_RETRY_WAIT'])
            download_file(_url, _track_info, _attempt + 1)
        else:
            print_exception(e, 'An exception occurred trying to download file url [{}]:'.format(_url))
    except Exception as e:
        print_exception(e, 'An exception occurred trying to download file url [{}]:'.format(_url))
    return file_path

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
    return file_path.endswith('.zip')


if __name__ == '__main__':
    sys.exit(main())

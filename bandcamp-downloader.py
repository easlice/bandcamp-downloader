#!/usr/bin/env python3

import argparse
import html
import http
import json
import os
import re
import sys
import time
import urllib.parse
import traceback

from concurrent.futures import ThreadPoolExecutor

# These require pip installs
from bs4 import BeautifulSoup, SoupStrainer
import requests
import browser_cookie3
from tqdm import tqdm

USER_URL = 'https://bandcamp.com/{}'
COLLECTION_POST_URL = 'https://bandcamp.com/api/fancollection/1/collection_items'
FILENAME_REGEX = re.compile('filename\\*=UTF-8\'\'(.*)')
WINDOWS_DRIVE_REGEX = re.compile(r'[a-zA-Z]:\\')
SANATIZE_PATH_WINDOWS_REGEX = re.compile(r'[<>:"/|?*\\]')
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
    parser.add_argument('--cookies', type=str, help='Path to cookies.txt')
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
    CONFIG['FORMAT'] = args.format
    CONFIG['FORCE'] = args.force
    CONFIG['DRY_RUN'] = args.dry_run

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

    links = get_download_links_for_user(args.username)
    if CONFIG['VERBOSE']: print('Found [{}] links for [{}]\'s collection.'.format(len(links), args.username))
    if not links:
        print('WARN: No album links found for user [{}]. Are you logged in and have you selected the correct browser to pull cookies from?'.format(args.username))
        sys.exit(2)

    print('Starting album downloads...')
    CONFIG['TQDM'] = tqdm(links, unit = 'album')
    if args.parallel_downloads > 1:
        with ThreadPoolExecutor(max_workers = args.parallel_downloads) as executor:
            executor.map(download_album, links)
    else:
        for link in links:
            download_album(link)
    CONFIG['TQDM'].close()
    print('Done.')

def generate_collection_post_payload(_user_info : dict) -> None:
    return {
        'fan_id' : _user_info['user_id'],
        'count' : _user_info['collection_count'] - len(_user_info['download_urls']),
        'older_than_token' : _user_info['last_token'],
    }

def get_user_collection(_user_info : dict) -> None:
    with requests.post(
        COLLECTION_POST_URL,
        data = json.dumps(generate_collection_post_payload(_user_info)),
        cookies = get_cookies(),
    ) as response:
        response.raise_for_status()
        data = json.loads(response.text)
        _user_info['download_urls'] += data['redownload_urls'].values()

def get_download_links_for_user(_user : str) -> [str]:
    print('Retrieving album links from user [{}]\'s collection.'.format(_user))

    soup = BeautifulSoup(
        requests.get(
            USER_URL.format(_user),
            cookies = get_cookies()
        ).text,
        'html.parser',
        parse_only = SoupStrainer('div', id='pagedata'),
    )
    div = soup.find('div')
    if not div:
        print('ERROR: No div with pagedata found for user at url [{}]'.format(USER_URL.format(_user)))
        return
    data = json.loads(html.unescape(div.get('data-blob')))
    if 'collection_count' not in data:
        print('ERROR: No collection info for user {}.\nPlease double check that your given username is correct.\nIt should be given exactly as it appears at the end of your bandcamp user url.\nFor example: bandcamp.com/user_name'.format(
            _user
        ))
        exit(2)

    user_info = {
        'collection_count' : data['collection_count'],
        'user_id' : data['fan_data']['fan_id'],
        'last_token' : data['collection_data']['last_token'],
    }
    user_info['download_urls'] = [ *data['collection_data']['redownload_urls'].values() ]

    get_user_collection(user_info)
    return user_info['download_urls']

def download_album(_album_url : str, _attempt : int = 1) -> None:
    try:
        soup = BeautifulSoup(
            requests.get(
                _album_url,
                cookies = get_cookies()
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
        download_file(download_url, track_info)
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

def download_file(_url : str, _track_info : dict = None, _attempt : int = 1) -> None:
    try:
        with requests.get(
                _url,
                cookies = get_cookies(),
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
                        return
                    else:
                        if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('Album at [{}] is the wrong size. Expected [{}] but was [{}]. Re-downloading.'.format(file_path, expected_size, actual_size))

            if CONFIG['VERBOSE'] >= 2: CONFIG['TQDM'].write('Album being saved to [{}]'.format(file_path))
            if CONFIG['DRY_RUN']:
                return
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

def print_exception(_e : Exception, _msg : str = '') -> None:
    CONFIG['TQDM'].write('\nERROR: {}'.format(_msg))
    CONFIG['TQDM'].write('\n'.join(traceback.format_exception(_e)))
    CONFIG['TQDM'].write('\n')


# Windows has some picky requirements about file names
# So let's replace known bad characters with '-'
def sanitize_filename(_path : str) -> str:
    if sys.platform.startswith('win'):
        # Ok, we need to leave on the ':' if it is like 'D:\'
        # otherwise, we need to remove it.
        new_path = ''
        search_path = _path
        if WINDOWS_DRIVE_REGEX.match(_path):
            new_path += _path[0:3]
            search_path = _path[3:]
        new_path += SANATIZE_PATH_WINDOWS_REGEX.sub('-', search_path)
        return new_path
    else:
        # Remove `/`
        return _path.replace('/', '-')

def get_cookies():
    if CONFIG['COOKIES']:
        cj = http.cookiejar.MozillaCookieJar(CONFIG['COOKIES'])
        cj.load()
        return cj

    try:
        func = getattr(browser_cookie3, CONFIG['BROWSER'])
        return func(domain_name = 'bandcamp.com')
    except AttributeError:
        raise Exception('Browser type [{}] is unknown. Can\'t pull cookies, so can\'t authenticate with bandcamp.'.format(CONFIG['BROWSER']))

if __name__ == '__main__':
    sys.exit(main())

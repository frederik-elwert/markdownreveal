import re
import os
import tarfile
import collections
from pathlib import Path
from subprocess import check_output
from urllib.request import urlretrieve
from threading import Timer
from pkg_resources import resource_filename
from typing import Any
from typing import List
from typing import Dict

import yaml
import requests
from pypandoc import convert_file
from livereload import Server
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.inotify_buffer import InotifyBuffer


__version__ = '0.0.1'

Config = Dict[Any, Any]
TarMembers = List[tarfile.TarInfo]


def update_config(template: Config, config: Config) -> Config:
    """
    Update a dictionary with key-value pairs found in another one.

    It is a recursive update.

    Parameters
    ----------
    template
        Initial dictionary to update.
    config
        New dictionary to update the original with.

    Returns
    -------
        The resulting (updated) dictionary.
    """
    for key, value in config.items():
        if isinstance(value, collections.Mapping):
            recurse = update_config(template.get(key, {}), value)
            template[key] = recurse
        else:
            template[key] = config[key]
    return template


def load_config() -> Config:
    """
    Load configuration file template.

    Returns
    -------
        The configuration file template.
    """
    config_template = resource_filename(__name__, 'config.template.yaml')
    config = yaml.load(open(config_template))
    config_file = Path('config.yaml')
    if config_file.exists():
        print('loading configuration file...')
        update_config(config, yaml.load(open(config_file)))
    config['local_path'] = Path.home() / config['local_path']
    config['output_path'] = config['local_path'] / 'out'
    config['reveal_extra']['theme'] = config['theme']
    print(config.items())
    return config


def latest_revealjs_release() -> str:
    """
    Fetch the latest reveal.js release tag.
    """
    releases = 'https://api.github.com/repos/hakimel/reveal.js/releases'
    latest = requests.get(releases).json()[0]
    return latest['tag_name']


def clean_revealjs_tar_members(members: TarMembers) -> TarMembers:
    """
    Strip .tar components (i.e.: remove top-level directory) from members.

    Parameters
    ----------
    members
        A list of .tar members.

    Returns
    -------
        A clean .tar member list with the stripped components.
    """
    clean = []
    for member in members:
        path = Path(member.name)
        if path.is_absolute():
            continue
        parts = path.parts[1:]
        if not parts:
            continue
        if parts[0] == 'test':
            continue
        member.name = str(Path(*parts))
        clean.append(member)
    return clean


def initialize_localdir(localdir: Path, reveal_version: str) -> Path:
    """
    Initialize local directory with the required reveal.js files.

    Parameters
    ----------
    localdir
        Path to store local files.
    reveal_version
        String with the reveal.js version to use (i.e.: `3.0.1`). The value
        `latest` is also allowed.

    Returns
    -------
        Path where output files will be generated, with a symbolic link to
        the corresponding reveal.js downloaded files.
    """
    # Initialize local directory
    outdir = localdir / 'out'
    outdir.mkdir(parents=True, exist_ok=True)
    # Download reveal.js
    reveal_path = localdir / reveal_version
    if not reveal_path.exists():
        if reveal_version == 'latest':
            reveal_version = latest_revealjs_release()
        url = 'https://github.com/hakimel/reveal.js/archive/%s.tar.gz'
        url = url % reveal_version
        reveal_tar, headers = urlretrieve(url)
        with tarfile.open(reveal_tar) as tar:
            members = clean_revealjs_tar_members(tar.getmembers())
            tar.extractall(reveal_path, members)
        os.remove(reveal_tar)
    symlink = outdir / 'revealjs'
    if symlink.exists():
        symlink.unlink()
    symlink.symlink_to(reveal_path, target_is_directory=True)
    return outdir


def find_indexes(haystack: List[str], regex: str) -> List[int]:
    """
    Find indexes in a list where a regular expression matches.

    Parameters
    ----------
    haystack
        List of strings.
    regex
        Regular expression to match.

    Returns
    -------
        The indexes where the regular expression was found.
    """
    return [i for i, item in enumerate(haystack) if re.search(regex, item)]


def markdown_to_reveal(input_file: Path, reveal_extra: Config) -> str:
    """
    Transform a Markdown input file to an HTML (reveal.js) output string.

    Parameters
    ----------
    input_file
        Input file path, containing the Markdown code.
    reveal_extra
        Extra configuration parameters for reveal.js.

    Returns
    -------
        The converted string.
    """
    extra_args = [
        '-s',
        '--slide-level=2',
        '-V', 'revealjs-url=revealjs',
    ]
    for key, value in reveal_extra.items():
        extra_args.extend(['-V', '%s=%s' % (key, value)])
    output = convert_file(
        source_file=str(input_file),
        format='md',
        to='revealjs',
        extra_args=extra_args,
    )
    return output


def tweak_html(html, config):
    """
    TODO
    """
    html = html.splitlines()
    if config['custom_css']:
        index = find_indexes(html, 'stylesheet.*id="theme"')[0]
        text = '<link rel="stylesheet" href="%s">' % config['custom_css']
        html.insert(index + 1, text)
    if Path(config['warmup']).exists():
        # TODO: maybe set warmup parameter as a URL? (autogenerate QR code)
        text = '<section><img src="%s" /></section>' % config['warmup']
        index = find_indexes(html, 'div class="slides"')[0]
        html.insert(index + 1, text)
    if config['footer']:
        text = '<div class="footer">%s</div>' % config['footer']
        for index in find_indexes(html, '<div class=\"reveal\">'):
            html.insert(index + 1, text)
    if config['header']:
        text = '<div class="header">%s</div>' % config['header']
        for index in find_indexes(html, '<div class=\"reveal\">'):
            html.insert(index + 1, text)
    if Path(config['logo']).exists():
        text = '<div class="logo"><img src="%s" /></div>' % config['logo']
        for index in find_indexes(html, '<div class=\"reveal\">'):
            html.insert(index + 1, text)
    if Path(config['background']).exists():
        text = '<section data-background="%s">' % config['background']
        for index in find_indexes(html, '<section>'):
            html[index] = html[index].replace('<section>', text)
    return '\n'.join(html)


def generate():
    """
    TODO
    """
    # Reload config
    config = load_config()
    print('Configuration: ', config)

    # Initialize localdir
    reveal_path = initialize_localdir(config['local_path'],
                                      config['reveal_version'])
    print('Using reveal.js found in %s' % reveal_path)

    # TODO:
    #   - input file as arg
    input_file = Path('presentation.md')

    # rsync
    command = [
        'rsync',
        '--delete',
        '--exclude', 'revealjs',
        '--exclude', '.git',
        '-av',
        '%s/' % input_file.resolve().parent,
        '%s/' % config['output_path'],
    ]
    check_output(command)

    # Convert from markdown
    print('Converting to markdown...')
    output = markdown_to_reveal(input_file,
                                reveal_extra=config['reveal_extra'])

    # HTML substitution
    output = tweak_html(output, config)

    # Write index.html
    print('Writing index.html...')
    index = config['output_path'] / 'index.html'
    index.write_text(output)

    print('Reloading...')
    with open(config['output_path'] / '.reload', 'a') as f:
        f.write('x\n')


# Reduce watchdog default buffer delay for faster response times
InotifyBuffer.delay = 0.1


class Handler(FileSystemEventHandler):
    def __init__(self, period=0.1):
        self.period = period
        self.timer = None
        self.set_timer()

    def on_any_event(self, event):
        print(event)
        if self.timer.is_alive():
            return
        self.set_timer()
        self.timer.start()

    def set_timer(self):
        self.timer = Timer(self.period, generate)


def run():
    """
    TODO
    """
    observer = Observer()
    handler = Handler()
    # Initial generation
    generate()
    observer.schedule(handler, '.', recursive=True)
    observer.start()

    server = Server()
    config = load_config()
    server.watch(str(config['output_path'] / '.reload'), delay=0)
    server.serve(
        root=config['output_path'],
        restart_delay=0,
        debug=True,
        open_url=True,
        open_url_delay=0,
        host='localhost',
        port=8123,
    )
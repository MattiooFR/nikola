# -*- coding: utf-8 -*-
# Copyright (c) 2012 Roberto Alsina y otros.

# Permission is hereby granted, free of charge, to any
# person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the
# Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the
# Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice
# shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
# KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
# PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS
# OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR
# OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Utility functions."""

from __future__ import print_function, unicode_literals
from collections import defaultdict, Callable
import datetime
import hashlib
import os
import re
import codecs
import json
import shutil
import subprocess
import sys
from zipfile import ZipFile as zip
try:
    from imp import reload
except ImportError:
    pass

import pytz

if sys.version_info[0] == 3:
    # Python 3
    bytes_str = bytes
    unicode_str = str
    unichr = chr
else:
    bytes_str = str
    unicode_str = unicode  # NOQA

from doit import tools
from unidecode import unidecode

import PyRSS2Gen as rss

__all__ = ['get_theme_path', 'get_theme_chain', 'load_messages', 'copy_tree',
           'generic_rss_renderer', 'copy_file', 'slugify', 'unslugify',
           'to_datetime', 'apply_filters', 'config_changed', 'get_crumbs']


class CustomEncoder(json.JSONEncoder):
    def default(self, obj):
        try:
            return json.JSONEncoder.default(self, obj)
        except TypeError:
            s = repr(obj).split('0x', 1)[0]
            return s


class config_changed(tools.config_changed):
    """ A copy of doit's but using pickle instead of serializing manually."""

    def _calc_digest(self):
        if isinstance(self.config, str):
            return self.config
        elif isinstance(self.config, dict):
            data = json.dumps(self.config, cls=CustomEncoder)
            if isinstance(data, str):  # pragma: no cover # python3
                byte_data = data.encode("utf-8")
            else:
                byte_data = data
            return hashlib.md5(byte_data).hexdigest()
        else:
            raise Exception('Invalid type of config_changed parameter -- got '
                            '{0}, must be string or dict'.format(type(
                                self.config)))

    def __repr__(self):
        return "Change with config: {0}".format(json.dumps(self.config,
                                                           cls=CustomEncoder))


def get_theme_path(theme):
    """Given a theme name, returns the path where its files are located.

    Looks in ./themes and in the place where themes go when installed.
    """
    dir_name = os.path.join('themes', theme)
    if os.path.isdir(dir_name):
        return dir_name
    dir_name = os.path.join(os.path.dirname(__file__),
                            'data', 'themes', theme)
    if os.path.isdir(dir_name):
        return dir_name
    raise Exception("Can't find theme '{0}'".format(theme))


def get_template_engine(themes):
    for theme_name in themes:
        engine_path = os.path.join(get_theme_path(theme_name), 'engine')
        if os.path.isfile(engine_path):
            with open(engine_path) as fd:
                return fd.readlines()[0].strip()
    # default
    return 'mako'


def get_theme_chain(theme):
    """Create the full theme inheritance chain."""
    themes = [theme]

    def get_parent(theme_name):
        parent_path = os.path.join(get_theme_path(theme_name), 'parent')
        if os.path.isfile(parent_path):
            with open(parent_path) as fd:
                return fd.readlines()[0].strip()
        return None

    while True:
        parent = get_parent(themes[-1])
        # Avoid silly loops
        if parent is None or parent in themes:
            break
        themes.append(parent)
    return themes


def load_messages(themes, translations):
    """ Load theme's messages into context.

    All the messages from parent themes are loaded,
    and "younger" themes have priority.
    """
    messages = defaultdict(dict)
    warned = []
    oldpath = sys.path[:]
    for theme_name in themes[::-1]:
        msg_folder = os.path.join(get_theme_path(theme_name), 'messages')
        default_folder = os.path.join(get_theme_path('default'), 'messages')
        sys.path.insert(0, default_folder)
        sys.path.insert(0, msg_folder)
        english = __import__('messages_en')
        for lang in list(translations.keys()):
            # If we don't do the reload, the module is cached
            translation = __import__('messages_' + lang)
            reload(translation)
            if sorted(translation.MESSAGES.keys()) !=\
                sorted(english.MESSAGES.keys()) and \
                    lang not in warned:
                # FIXME: get real logging in place
                print("Warning: Incomplete translation for language "
                      "'{0}'.".format(lang))
                warned.append(lang)
            messages[lang].update(english.MESSAGES)
            messages[lang].update(translation.MESSAGES)
            del(translation)
    sys.path = oldpath
    return messages


def copy_tree(src, dst, link_cutoff=None):
    """Copy a src tree to the dst folder.

    Example:

    src = "themes/default/assets"
    dst = "output/assets"

    should copy "themes/defauts/assets/foo/bar" to
    "output/assets/foo/bar"

    if link_cutoff is set, then the links pointing at things
    *inside* that folder will stay as links, and links
    pointing *outside* that folder will be copied.
    """
    ignore = set(['.svn'])
    base_len = len(src.split(os.sep))
    for root, dirs, files in os.walk(src):
        root_parts = root.split(os.sep)
        if set(root_parts) & ignore:
            continue
        dst_dir = os.path.join(dst, *root_parts[base_len:])
        if not os.path.isdir(dst_dir):
            os.makedirs(dst_dir)
        for src_name in files:
            if src_name == '.DS_Store':
                continue
            dst_file = os.path.join(dst_dir, src_name)
            src_file = os.path.join(root, src_name)
            yield {
                'name': str(dst_file),
                'file_dep': [src_file],
                'targets': [dst_file],
                'actions': [(copy_file, (src_file, dst_file, link_cutoff))],
                'clean': True,
            }


def generic_rss_renderer(lang, title, link, description, timeline, output_path,
                         rss_teasers):
    """Takes all necessary data, and renders a RSS feed in output_path."""
    items = []
    for post in timeline[:10]:
        args = {
            'title': post.title(lang),
            'link': post.permalink(lang, absolute=True),
            'description': post.text(lang, teaser_only=rss_teasers),
            'guid': post.permalink(lang, absolute=True),
            # PyRSS2Gen's pubDate is GMT time.
            'pubDate': (post.date if post.date.tzinfo is None else
                        post.date.astimezone(pytz.timezone('UTC'))),
        }
        items.append(rss.RSSItem(**args))
    rss_obj = rss.RSS2(
        title=title,
        link=link,
        description=description,
        lastBuildDate=datetime.datetime.now(),
        items=items,
        generator='nikola',
    )
    dst_dir = os.path.dirname(output_path)
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    with codecs.open(output_path, "wb+", "utf-8") as rss_file:
        data = rss_obj.to_xml(encoding='utf-8')
        if isinstance(data, bytes_str):
            data = data.decode('utf-8')
        rss_file.write(data)


def copy_file(source, dest, cutoff=None):
    dst_dir = os.path.dirname(dest)
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    if os.path.islink(source):
        link_target = os.path.relpath(
            os.path.normpath(os.path.join(dst_dir, os.readlink(source))))
        # Now we have to decide if we copy the link target or the
        # link itself.
        if cutoff is None or not link_target.startswith(cutoff):
            # We copy
            shutil.copy2(source, dest)
        else:
            # We link
            if os.path.exists(dest) or os.path.islink(dest):
                os.unlink(dest)
            os.symlink(os.readlink(source), dest)
    else:
        shutil.copy2(source, dest)


def remove_file(source):
    if os.path.isdir(source):
        shutil.rmtree(source)
    elif os.path.isfile(source) or os.path.islink(source):
        os.remove(source)

# slugify is copied from
# http://code.activestate.com/recipes/
# 577257-slugify-make-a-string-usable-in-a-url-or-filename/
_slugify_strip_re = re.compile(r'[^\w\s-]')
_slugify_hyphenate_re = re.compile(r'[-\s]+')


def slugify(value):
    """
    Normalizes string, converts to lowercase, removes non-alpha characters,
    and converts spaces to hyphens.

    From Django's "django/template/defaultfilters.py".

    >>> print(slugify('\xe1\xe9\xed.\xf3\xfa'))
    aeiou

    >>> print(slugify('foo/bar'))
    foobar

    >>> print(slugify('foo bar'))
    foo-bar

    """
    value = unidecode(value)
    # WARNING: this may not be python2/3 equivalent
    # value = unicode(_slugify_strip_re.sub('', value).strip().lower())
    value = str(_slugify_strip_re.sub('', value).strip().lower())
    return _slugify_hyphenate_re.sub('-', value)


def unslugify(value):
    """
    Given a slug string (as a filename), return a human readable string
    """
    value = re.sub('^[0-9]', '', value)
    value = re.sub('([_\-\.])', ' ', value)
    value = value.strip().capitalize()
    return value


# A very slightly safer version of zip.extractall that works on
# python < 2.6

class UnsafeZipException(Exception):
    pass


def extract_all(zipfile):
    pwd = os.getcwd()
    os.chdir('themes')
    with zip(zipfile) as z:
        namelist = z.namelist()
        for f in namelist:
            if f.endswith('/') and '..' in f:
                raise UnsafeZipException('The zip file contains ".." and is '
                                         'not safe to expand.')
        for f in namelist:
            if f.endswith('/'):
                if not os.path.isdir(f):
                    try:
                        os.makedirs(f)
                    except:
                        raise OSError("Failed making {0} directory "
                                      "tree!".format(f))
            else:
                z.extract(f)
    os.chdir(pwd)


# From https://github.com/lepture/liquidluck/blob/develop/liquidluck/utils.py
def to_datetime(value, tzinfo=None):
    if isinstance(value, datetime.datetime):
        return value
    supported_formats = [
        '%Y/%m/%d %H:%M',
        '%Y/%m/%d %H:%M:%S',
        '%Y/%m/%d %I:%M:%S %p',
        '%a %b %d %H:%M:%S %Y',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%dT%H:%M',
        '%Y%m%d %H:%M:%S',
        '%Y%m%d %H:%M',
        '%Y-%m-%d',
        '%Y%m%d',
    ]
    for format in supported_formats:
        try:
            dt = datetime.datetime.strptime(value, format)
            if tzinfo is None:
                return dt
            # Build a localized time by using a given timezone.
            return tzinfo.localize(dt)
        except ValueError:
            pass
    raise ValueError('Unrecognized date/time: {0!r}'.format(value))


def apply_filters(task, filters):
    """
    Given a task, checks its targets.
    If any of the targets has a filter that matches,
    adds the filter commands to the commands of the task,
    and the filter itself to the uptodate of the task.
    """

    def filter_matches(ext):
        for key, value in list(filters.items()):
            if isinstance(key, (tuple, list)):
                if ext in key:
                    return value
            elif isinstance(key, (str, bytes)):
                if ext == key:
                    return value
            else:
                assert False, key

    for target in task['targets']:
        ext = os.path.splitext(target)[-1].lower()
        filter_ = filter_matches(ext)
        if filter_:
            for action in filter_:
                def unlessLink(action, target):
                    if not os.path.islink(target):
                        if isinstance(action, Callable):
                            action(target)
                        else:
                            subprocess.check_call(action % target, shell=True)

                task['actions'].append((unlessLink, (action, target)))
    return task


def get_crumbs(path, is_file=False):
    """Create proper links for a crumb bar.

    >>> crumbs = get_crumbs('galleries')
    >>> len(crumbs)
    1
    >>> print('|'.join(crumbs[0]))
    #|galleries

    >>> crumbs = get_crumbs(os.path.join('galleries','demo'))
    >>> len(crumbs)
    2
    >>> print('|'.join(crumbs[0]))
    ..|galleries
    >>> print('|'.join(crumbs[1]))
    #|demo

    >>> crumbs = get_crumbs(os.path.join('listings','foo','bar'), is_file=True)
    >>> len(crumbs)
    3
    >>> print('|'.join(crumbs[0]))
    ..|listings
    >>> print('|'.join(crumbs[1]))
    .|foo
    >>> print('|'.join(crumbs[2]))
    #|bar
    """

    crumbs = path.split(os.sep)
    _crumbs = []
    if is_file:
        for i, crumb in enumerate(crumbs[-3::-1]):  # Up to parent folder only
            _path = '/'.join(['..'] * (i + 1))
            _crumbs.append([_path, crumb])
        _crumbs.insert(0, ['.', crumbs[-2]])  # file's folder
        _crumbs.insert(0, ['#', crumbs[-1]])  # file itself
    else:
        for i, crumb in enumerate(crumbs[::-1]):
            _path = '/'.join(['..'] * i) or '#'
            _crumbs.append([_path, crumb])
    return list(reversed(_crumbs))

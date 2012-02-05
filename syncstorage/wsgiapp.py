# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
"""
Application entry point.
"""
from webob.exc import HTTPServiceUnavailable

from services.baseapp import set_app, SyncServerApp
from services.wsgiauth import Authentication
from syncstorage.controller import StorageController
from syncstorage.storage import get_storage

try:
    from memcache import Client
except ImportError:
    Client = None       # NOQA

_EXTRAS = {'auth': True}


def _url(url):
    for pattern, replacer in (('_API_', '{api:1.0|1|1.1}'),
                              ('_COLLECTION_',
                               '{collection:[a-zA-Z0-9._-]+}'),
                              ('_USERNAME_',
                               '{username:[a-zA-Z0-9._-]+}'),
                              ('_ITEM_',
                              r'{item:[\\a-zA-Z0-9._?#~-]+}')):
        url = url.replace(pattern, replacer)
    return url


urls = [('GET', _url('/_API_/_USERNAME_/info/collections'),
         'storage', 'get_collections', _EXTRAS),
        ('GET', _url('/_API_/_USERNAME_/info/collection_counts'),
         'storage', 'get_collection_counts', _EXTRAS),
        ('GET', _url('/_API_/_USERNAME_/info/quota'), 'storage', 'get_quota',
          _EXTRAS),
        ('GET', _url('/_API_/_USERNAME_/info/collection_usage'), 'storage',
         'get_collection_usage', _EXTRAS),
        # XXX empty collection call
        ('PUT', _url('/_API_/_USERNAME_/storage/'), 'storage', 'get_storage',
         _EXTRAS),
        ('GET', _url('/_API_/_USERNAME_/storage/_COLLECTION_'), 'storage',
        'get_collection', _EXTRAS),
        ('GET', _url('/_API_/_USERNAME_/storage/_COLLECTION_/_ITEM_'),
         'storage', 'get_item', _EXTRAS),
        ('PUT', _url('/_API_/_USERNAME_/storage/_COLLECTION_/_ITEM_'),
         'storage', 'set_item', _EXTRAS),
        ('POST', _url('/_API_/_USERNAME_/storage/_COLLECTION_'), 'storage',
        'set_collection', _EXTRAS),
        ('PUT', _url('/_API_/_USERNAME_/storage/_COLLECTION_'), 'storage',
        'set_collection', _EXTRAS),
        ('DELETE', _url('/_API_/_USERNAME_/storage/_COLLECTION_'), 'storage',
        'delete_collection', _EXTRAS),
        ('DELETE', _url('/_API_/_USERNAME_/storage/_COLLECTION_/_ITEM_'),
         'storage', 'delete_item', _EXTRAS),
        ('DELETE', _url('/_API_/_USERNAME_/storage'), 'storage',
         'delete_storage', _EXTRAS)]

controllers = {'storage': StorageController}


class StorageServerApp(SyncServerApp):
    """Storage application"""
    def __init__(self, urls, controllers, config=None,
                 auth_class=Authentication):
        super(StorageServerApp, self).__init__(urls, controllers, config,
                                               auth_class)
        self.config = config

        # Collecting the host-specific config and building connectors.
        self.storages = {'default': get_storage(config)}
        hostnames = set()
        host_token = 'host:'
        for cfgkey in config:
            if cfgkey.startswith(host_token):
                # Get the hostname from the config key.  This assumes
                # that host-specific keys have two trailing components
                # that specify the setting to override.
                # E.g: "host:localhost.storage.sqluri" => "localhost"
                hostname = cfgkey[len(host_token):].rsplit(".", 2)[0]
                hostnames.add(hostname)
        for hostname in hostnames:
            host_cfg = self._host_specific(hostname, config)
            self.storages[hostname] = get_storage(host_cfg)

        self.check_blacklist = \
                self.config.get('storage.check_blacklisted_nodes', False)
        if self.check_blacklist and Client is not None:
            servers = self.config.get('storage.cache_servers',
                                      '127.0.0.1:11211')
            self.cache = Client(servers.split(','))
        else:
            if self.check_blacklist:
                raise ValueError('The "check_blacklisted_node" option '
                                 'needs a memcached server')
            self.cache = None

    def get_storage(self, request):
        host = request.host
        if host not in self.storages:
            host = 'default'
        return self.storages[host]

    def _before_call(self, request):
        # let's control if this server is not on the blacklist
        if not self.check_blacklist:
            return {}

        host = request.host
        if self.cache.get('down:%s' % host) is not None:
            # the server is marked as down -- let's exit
            raise HTTPServiceUnavailable("Server Problem Detected")

        backoff = self.cache.get('backoff:%s' % host)
        if backoff is not None:
            # the server is marked to back-off requests. We will treat those
            # but add the header
            return {'X-Weave-Backoff': str(backoff)}
        return {}

    def _debug_server(self, request):
        res = []
        storage = self.get_storage(request)
        res.append('- backend: %s' % storage.get_name())
        if storage.get_name() in ('memcached',):
            cache_servers = ['%s:%d' % (server.ip, server.port)
                             for server in storage.cache.servers]
            res.append('- memcached servers: %s</li>' % \
                    ', '.join(cache_servers))

        if storage.get_name() in ('sql', 'memcached'):
            res.append('- sqluri: %s' % storage.sqluri)
        return res


make_app = set_app(urls, controllers, klass=StorageServerApp,
                   auth_class=Authentication)

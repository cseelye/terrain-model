
#pylint: disable=unidiomatic-typecheck,protected-access,global-statement

from pyapputil.logutil import GetLogger
from pyapputil.exceptutil import ApplicationError

import base64
import socket
import ssl

# Python 2/3 compat imports
try:
    import urlparse
except ImportError:
    import urllib.parse as urlparse

try:
    import httplib
except:
    import http.client as httplib

try:
    from urllib2 import urlopen, Request, HTTPError, URLError
except ImportError:
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError, URLError



class UnauthorizedError(ApplicationError):
    pass


class HTTPDownloader(object):
    """Helper for downloading content from a URL"""

    def __init__(self, server, port=443, username=None, password=None):
        """
        Args:
            server:             the IP address or resolvable hostname of the server to download from
            port:               the port to use
            username:           the name of an authorized user
            password:           the password of the user
        """
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.log = GetLogger()

    def Download(self, remotePath, useAuth=True, useSSL=True, timeout=300):
        """
        Download a URL (GET) and return the content. For large binary files, see StreamingDownload

        Args:
            remotePath:     the path component of the URL
            useAuth:        use Basic Auth when connecting
            useSSL:         Use SSL when connecting
            timeout:        how long to stay connected before abandoning the transfer

            The download URL will be constructed like http[s]://self.server:port/remotePath

        Returns:
            The content retrieved from the URL
        """
        response = self._open(remotePath, useAuth, useSSL, timeout)
        dl = response.read()
        response.close()
        return dl

    def StreamingDownload(self, remotePath, localFile, useAuth=True, useSSL=True, timeout=300):
        """
        Download a URL (GET) to a file.  Suitable for large/binary files

        Args:
            remotePath:     the path component of the URL
            localPath:      fully qualified path to the local file to save the content in. The directory
                            component of the path must already exist
            useAuth:        use Basic Auth when connecting
            useSSL:         Use SSL when connecting
            timeout:        how long to stay connected before abandoning the transfer

            The download URL will be constructed like https://self.server:port/remotePath
        """
        response = self._open(remotePath, useAuth, useSSL, timeout)
        with open(localFile, 'w') as handle:
            while True:
                try:
                    chunk = response.read(16 * 1024)
                except (socket.timeout, socket.error, socket.herror, socket.gaierror) as ex:
                    raise ConnectionError(self.server, endpoint, ex)

                if not chunk:
                    break
                try:
                    handle.write(chunk)
                except IOError as ex:
                    raise LocalEnvironmentError(ex)

    def _open(self, remotePath, useAuth=True, useSSL=True, timeout=300):
        """Common code for Download and StreamingDownload"""
        context = None
        if useSSL:
            endpoint = urlparse.urljoin('https://{}:{}/'.format(self.server, self.port), remotePath)

            try:
                # pylint: disable=no-member
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                # pylint: enable=no-member
            except AttributeError:
                pass

        else:
            endpoint = urlparse.urljoin('http://{}:{}/'.format(self.server, self.port), remotePath)

        request = Request(endpoint)
        if useAuth and self.username:
            request.add_header('Authorization', "Basic " + base64.encodestring('{}:{}'.format(self.username, self.password)).strip())

        self.log.debug2('Downloading {}'.format(endpoint))
        try:
            if context:
                # pylint: disable=unexpected-keyword-arg
                response = urlopen(request, timeout=timeout, context=context)
                # pylint: enable=unexpected-keyword-arg
            else:
                response = urlopen(request, timeout=timeout)
        except (socket.timeout, socket.error, socket.herror, socket.gaierror) as ex:
            raise ApplicationError("Could not connect to {}: {}".format(self.server, ex.args[1]), innerException=ex)
        except HTTPError as ex:
            if ex.code == 401:
                raise UnauthorizedError("Could not connect to {}: Unauthorized".format(self.server), innerException=ex)
            else:
                raise ApplicationError("Could not connect to {}: HTTP error {} - {}".format(self.server, ex.code, ex.reason), innerException=ex)
        except URLError as ex:
            if type(ex.reason) in [socket.timeout, socket.error, socket.herror, socket.gaierror]:
                raise ApplicationError("Could not connect to {}: {}".format(self.server, ex.reason.args[1]), innerException=ex)
            if type(ex.reason) == OSError:
                raise ApplicationError("Could not connect to {}: {}".format(self.server, ex.reason.strerror), innerException=ex)
            raise ApplicationError("Could not connect to {}: {}".format(self.server, ex.reason), innerException=ex)
        except httplib.BadStatusLine as ex:
            raise ApplicationError("Could not connect to {}: Bad HTTP status".format(self.server), innerException=ex)

        return response


    @staticmethod
    def DownloadURL(url, timeout=300):
        """Static version for one-off use when instantiating a class is too much work"""
        pieces = urlparse.urlparse(url)
        downloader = HTTPDownloader(pieces.netloc,
                                    443 if pieces.scheme == "https" else 80,
                                    pieces.username,
                                    pieces.password)
        return downloader.Download(pieces.path,
                                   useAuth=pieces.username != None,
                                   useSSL=pieces.scheme == "https",
                                   timeout=timeout)

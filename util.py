#pylint: disable=unidiomatic-typecheck,protected-access,global-statement
"""Helper functions for terrain-model scripts"""
try:
    import collections.abc as collections
except ImportError:
    import collections
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import time

from pyapputil.logutil import GetLogger
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError
from pyapputil.typeutil import IntegerRangeType, ItemList
import requests
import webcolors

def default_json(obj):
    """Default serializer for json.dumps"""
    if hasattr(obj, 'to_json'):
        return obj.to_json()
    raise TypeError(f'Object of type {obj.__class__.__name__} is not JSON serializable')


class MetadataFile:
    """Sidecar file to hold metadata"""
    def __init__(self, parent_file):
        self.meta = {}
        self.parent_file = Path(parent_file)
        self.meta_file = self.parent_file.with_suffix(self.parent_file.suffix + ".meta")
        self.add("cmdline", " ".join(sys.argv))
        self.add("help", """ Latitude:   -90s to 90n degrees, north positive, south negative, Y axis
 Longtitude: -180w to 180e degrees, east positive, west negative, X axis
 North: max_lat
 South: min_lat
 East: max_long
 West: min_long
 Upper Left = N,W or max_lat, min_long
 Lower Right = S,E or min_lat, max_long""")

    def add(self, key, value):
        self.meta[key] = value

    def write(self):
        self.meta["created"] = datetime.utcnow().replace(tzinfo=timezone.utc).astimezone().replace(microsecond=0).isoformat()
        self.meta_file.write_text(json.dumps(self.meta, default=default_json, indent=2), encoding="utf-8")

class UnauthorizedError(ApplicationError):
    """Raised when a 401 or similar error is encountered"""

def download_file(url, local_file):
    """
    Download a file from a URL. This function is made to stream large binary
    files.

    Args:
        url:            (string) The URL to download.
        local_file:     (Path)   The filepath to save the content to.
    """
    log = GetLogger()
    log.debug("GET %s -> %s", url, local_file)
    with requests.get(url, stream=True) as res:
        res.raise_for_status()
        with open(local_file, "wb") as output:
            for chunk in res.iter_content(chunk_size=16 * 1024):
                output.write(chunk)

def list_like(thing):
    """Check of the argument is an iterable but not a string"""
    return isinstance(thing, collections.Iterable) and not isinstance(thing, str)

class Color():
    """A named or RBG color"""

    def __init__(self):
        self.name = None
        self.rgb = None

    def __call__(self, inVal):
        self.parse(inVal)
        return self

    def __str__(self):
        return self.name

    def __repr__(self):
        if self.name:
            return f"Color({self.name})"
        if self.rgb:
            return f"Color({self.rgb})"
        return "Color"

    def to_json(self):
        if self.name:
            return str(self.name)
        if self.rgb:
            return str(self.rgb)

    def parse(self, color):
        """Initialize this Color object from a string color name or tuple of RGB"""

        if not color:
            raise InvalidArgumentError("color cannot be empty")

        if isinstance(color, Color):
            self.name = color.name
            self.rgb = color.rgb
            return self

        if (isinstance(color, collections.Sequence) and not isinstance(color, str)) or \
           (isinstance(color, str) and "," in color):
            self.rgb = ItemList(IntegerRangeType(minValue=0, maxValue=255), minLength=3, maxLength=3)(color)

        else:
            color = str(color)
            try:
                parsed = webcolors.name_to_rgb(color)
                self.name = color
                self.rgb = (parsed.red, parsed.green, parsed.blue)
            except ValueError:
                raise InvalidArgumentError("{color} is not a recognizable color name") #pylint: disable=raise-missing-from

        if not self.name:
            try:
                self.name = webcolors.rgb_to_name(self.rgb)
            except ValueError:
                self.name = None

        return self

    def as_bgr(self):
        """Return this color in BGR format"""
        return tuple(reversed(self.rgb))

class ProgressTracker:
    """Track and display progress of a long running operation"""
    def __init__(self, total, display_pct_interval=10, display_time_interval=180, log=None):
        self.start_time = time()
        self.count = 0
        self.last_display_pct = 0
        self.last_display_time = self.start_time
        self.time_per_unit = 0
        self.total = total
        self.display_pct_interval = display_pct_interval
        self.display_time_interval = display_time_interval
        self.logger = log
        if not self.logger:
            self.logger = GetLogger()

    def update(self, newcount, display=True):
        """Update the progress"""
        self.count = newcount
        if self.count > 0:
            self.time_per_unit = (time() - self.start_time) / self.count
        if display:
            self.display()

    def percent_complete(self):
        """Calculate and return the percent complete"""
        complete = int(self.count * 100 / self.total)
        if complete >= 100 and self.count < self.total:
            complete = 99
        return complete

    def est_time_remaining(self):
        """Calculate and retrun the estimated time remaining based on the speed
        so far"""
        return int(self.time_per_unit * (self.total - self.count))

    def time_since_start(self):
        """Calculate and return the elapsed time"""
        return time() - self.start_time

    def display(self):
        """Log the current progress"""
        now = time()
        current = self.percent_complete()
        time_left = self.est_time_remaining()
        elapsed_time = self.time_since_start()
        if current == self.last_display_pct:
            return
        if current >= 100 and self.last_display_pct <= 100:
            self.logger.info(f"   {current}% ({int(elapsed_time)} sec elapsed)")
        elif time_left > 0 and (current >= self.last_display_pct + self.display_pct_interval or \
             now - self.last_display_time > self.display_time_interval):
            self.logger.info(f"    {current}% - {time_left} sec remaining")
            self.last_display_pct = current
            self.last_display_time = now

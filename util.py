#pylint: disable=unidiomatic-typecheck,protected-access,global-statement
"""Helper functions for terrain-model scripts"""

import collections
from time import time

from pyapputil.logutil import GetLogger
from pyapputil.exceptutil import ApplicationError, InvalidArgumentError
from pyapputil.typeutil import IntegerRangeType, ItemList
import requests
import webcolors

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
    with requests.get(url, stream=True) as req:
        req.raise_for_status()
        with open(local_file, "wb") as output:
            for chunk in req.iter_content(chunk_size=16 * 1024):
                output.write(chunk)

class Color:
    """A named or RBG color"""

    def __init__(self):
        self.name = None
        self.rgb = None

    def __call__(self, inVal):
        self.parse(inVal)
        return self

    def __repr__(self):
        if self.name:
            return f"Color({self.name})"
        if self.rgb:
            return f"Color({self.rgb})"
        return "Color"

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
        elif current >= self.last_display_pct + self.display_pct_interval or \
             now - self.last_display_time > self.display_time_interval:
            self.logger.info(f"    {current}% - {time_left} sec remaining")
            self.last_display_pct = current
            self.last_display_time = now

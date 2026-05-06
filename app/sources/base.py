from __future__ import annotations

import logging
import warnings
from abc import ABC, abstractmethod

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

from app.config import (
    DEFAULT_REQUEST_HEADERS,
    REQUEST_BACKOFF_FACTOR,
    REQUEST_RETRIES,
    REQUEST_TIMEOUT,
)
from app.models import CollectedItem, SourceConfig


class BaseSource(ABC):
    def __init__(self, config: SourceConfig):
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.last_fetch_stats: dict[str, int] = {}
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_REQUEST_HEADERS)
        self.session.headers.update(config.request_headers)
        if config.user_agent:
            self.session.headers["User-Agent"] = config.user_agent
        retry = Retry(
            total=REQUEST_RETRIES,
            backoff_factor=REQUEST_BACKOFF_FACTOR,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    @abstractmethod
    def fetch_items(self) -> list[CollectedItem]:
        """Return collected items for the source."""

    def get(self, url: str) -> requests.Response:
        timeout = self.config.request_timeout or REQUEST_TIMEOUT
        if not self.config.verify_ssl:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                response = self.session.get(url, timeout=timeout, verify=False)
        else:
            response = self.session.get(url, timeout=timeout, verify=True)
        response.raise_for_status()
        return response

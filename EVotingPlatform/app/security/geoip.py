"""Geo-IP lookup (REQ-19): which country does an IP address belong to?

The prototype reads a small table from `geoip_demo_table.json`. The most specific
(longest) matching range wins. An address that is invalid or not in the table gives
`None`, and the access checks treat "unknown" as a refusal.

To use a real geo-IP service, call `set_provider(your_function)` where your function
takes an IP string and returns a two-letter country code or None.
"""
import ipaddress
import json
from functools import lru_cache
from pathlib import Path

DEFAULT_TABLE = Path(__file__).with_name('geoip_demo_table.json')


@lru_cache(maxsize=4)
def _load(path):
    ranges = json.loads(Path(path).read_text(encoding='utf-8'))['ranges']
    nets = [(ipaddress.ip_network(cidr, strict=False), country.upper()) for cidr, country in ranges.items()]
    return sorted(nets, key=lambda item: item[0].prefixlen, reverse=True)


def simulated_lookup(ip, table_path=DEFAULT_TABLE):
    try:
        address = ipaddress.ip_address((ip or '').strip())
    except ValueError:
        return None
    for network, country in _load(str(table_path)):
        if address in network:
            return country
    return None


_provider = simulated_lookup


def set_provider(fn):
    """Swap in a real geo-IP lookup. `fn(ip) -> 'AU' | None`."""
    global _provider
    _provider = fn


def reset_provider():
    global _provider
    _provider = simulated_lookup


def lookup_country(ip):
    return _provider(ip)
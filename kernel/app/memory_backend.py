"""Explicit opt-in HTTP adapter for embeddings/optional memory REST.

No request is sent unless both the opt-in and an exact endpoint are configured.
An endpoint can be remote; configuring it authorizes sending input there.
"""
import os
import requests

def _check(url, variable):
    endpoint=os.getenv(variable,'')
    if os.getenv('LIRA_MEMORY_ALLOW_NETWORK')!='1' or not endpoint or url!=endpoint:
        raise requests.RequestException('Memory HTTP backend disabled or endpoint not explicitly configured')

def post(url, **kwargs):
    _check(url,'LIRA_MEMORY_EMBED_URL')
    return requests.post(url,**kwargs)

def get(url, **kwargs):
    _check(url,'LIRA_MEMORY_REST')
    return requests.get(url,**kwargs)

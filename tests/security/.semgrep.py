"""Positive and negative regression examples for the project's Semgrep rules."""

import os
import subprocess

import requests
import yaml


def examples(data, command):
    # ruleid: python-no-unsafe-yaml
    yaml.load(data)
    # ruleid: python-no-unsafe-yaml
    yaml.load(data, Loader=yaml.Loader)
    # ruleid: python-no-unsafe-yaml
    yaml.unsafe_load(data)
    # ok: python-no-unsafe-yaml
    yaml.safe_load(data)
    # ruleid: python-no-shell-command
    subprocess.run(command, shell=True)
    # ruleid: python-no-shell-command
    os.system(command)
    # ok: python-no-shell-command
    subprocess.run(["echo", command], check=True)
    # ruleid: python-no-disabled-tls
    requests.get("https://example.org", verify=False)
    # ok: python-no-disabled-tls
    requests.get("https://example.org", timeout=10)
    # ruleid: python-no-eval
    eval(data)
    # ruleid: python-no-eval
    exec(data)
    # ok: python-no-eval
    int(data)

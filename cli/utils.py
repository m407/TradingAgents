"""Compatibility module forwarding to cli.prompts."""

import questionary  # noqa: F401
from dotenv import find_dotenv, set_key  # noqa: F401

from cli.prompts import *  # noqa: F401, F403

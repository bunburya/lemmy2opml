#!/usr/bin/env python
import logging
import os
import sys
from datetime import datetime
from email.utils import format_datetime
from getpass import getpass
from dataclasses import dataclass
from os.path import splitext
from time import sleep
from typing import Optional, Any, Union, Generator
from urllib.parse import urlsplit, urlencode, urlunsplit
from xml.etree import ElementTree
from argparse import ArgumentParser, Namespace

import requests
from opyml import Outline, OPML, Head, Body


class NotLoggedInError(Exception):
    pass


logger = logging.getLogger(__name__)


def _walk_outlines(top: Union[Body, Outline]) -> Generator[Outline, None, None]:
    """Recursively traverse a tree of :class:`Outline` objects, yielding any such object that does not have a type of
    "category".

    :param top: An object representing the top-level element in the tree.
    """
    for outline in top.outlines:
        if outline.type == "category":
            for child in _walk_outlines(outline):
                yield child
        else:
            yield outline


def to_https(url: str) -> str:
    """Convert a URL that lacks a scheme, or specifies a scheme other than HTTPS, to a https:// URL."""
    if url.startswith("https://"):
        return url
    elif url.startswith("http://"):
        return f"https://{url[7:]}"
    else:
        return f"https://{url}"


def prettify_xml(xml: str, space: str = "  ") -> str:
    """Takes XML as a string and outputs it nicely indented."""
    elem = ElementTree.XML(xml)
    ElementTree.indent(elem, space)
    return ElementTree.tostring(elem, encoding="unicode")


@dataclass(slots=True)
class SortBy:
    """A simple data class containing the string values that are used when telling Lemmy or Kbin how to sort posts.

    :param lemmy: The string to be used in a Lemmy URL. If None, that means the relevant sorting method is not supported
        by Lemmy.
    :param kbin: The string to be used in a Kbin URL.If None, that means the relevant sorting method is not supported
        by Kbin.
    """
    lemmy: Optional[str] = None
    kbin: Optional[str] = None


UNSUPPORTED_SORT_BY = SortBy(None, None)

SORT_BY_VALUES = {
    "top": SortBy("TopAll", "top"),
    "hot": SortBy("Hot", "hot"),
    "active": SortBy("Active", "active"),
    "new": SortBy("New", "newest"),
    "old": SortBy("Old", None),
    "mostcomments": SortBy("MostComments", "commented"),
    "newcomments": SortBy("NewComments", None)
}


@dataclass(slots=True)
class LemmyCommunity:
    """A class representing a single Lemmy community.

    :param instance: The Lemmy instance to which the community belongs.
    :param name: The name of the community (ie, what appears in the URL).
    :param id: The unique ID of the community.
    :param title: The human-readable title of the community.
    :param description: An optional description of the community.
    :param is_kbin: Whether the communities is actually a Kbin magazine.
    """
    instance: str
    name: str
    id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    is_kbin: bool = False

    @staticmethod
    def from_url(
            url: str,
            id: Optional[int] = None,
            title: Optional[str] = None,
            description: Optional[str] = None
    ) -> "LemmyCommunity":
        """Parse a Lemmy community URL to a :class:`LemmyCommunity` object."""
        parsed = urlsplit(url)
        path_tokens = parsed.path.split('/')[1:]
        if path_tokens[0] == "c":
            is_kbin = False
        elif path_tokens[0] == "m":
            is_kbin = True
        else:
            raise ValueError(f"URL does not appear to be a Lemmy community URL: {url}")
        return LemmyCommunity(
            instance=parsed.netloc,
            name=path_tokens[1],
            id=id,
            title=title,
            description=description,
            is_kbin=is_kbin
        )

    @staticmethod
    def from_feed_url(url: str) -> "LemmyCommunity":
        """Parse a Lemmy community RSS feed URL to a :class:`LemmyCommunity` object.

        :param url: A URL for the RSS feed of a Lemmy community, in the form
            `https://<instance>/feeds/c/<community>.xml`.
        """
        parsed = urlsplit(url)
        path_tokens = parsed.path.split('/')[1:]

        # Validate that URL path is of the correct form
        if path_tokens[0] != "feeds":
            raise ValueError(f"URL does not appear to be a Lemmy feed URL: {url}")
        elif path_tokens[1] != "c":
            raise ValueError(f"URL does not appear to be a Lemmy community feed URL: {url}")

        return LemmyCommunity(
            instance=parsed.netloc,
            name=splitext(path_tokens[2])[0]
        )

    @staticmethod
    def from_text(text: str, is_kbin: bool = False) -> "LemmyCommunity":
        """Parse a textual representation of a Lemmy community to a :class:`LemmyCommunity` object.

        :param text: Text in the form `!<community>@<instance>`.
        :param is_kbin: Whether the community is actually a Kbin magazine.
        """
        if text[0] != "!":
            raise ValueError(f"Invalid format: {text}")
        try:
            community, instance = text[1:].split("@")
        except ValueError:
            raise ValueError(f"Invalid format: {text}")
        return LemmyCommunity(
            instance,
            community,
            is_kbin=is_kbin
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "LemmyCommunity":
        """Parse a dict that has been generated by parsing the JSON returned by the Lemmy API into a
        :class:`LemmyCommunity` object.

        :param data: A dict describing the community.
        """
        return LemmyCommunity.from_url(
            url=data["actor_id"],
            id=data["id"],
            title=data.get("title"),
            description=data.get("description")
        )

    @staticmethod
    def from_outline(outline: Outline) -> "LemmyCommunity":
        """Generate a :class:`LemmyCommunity` object from an :class:`Outline` object."""
        if outline.type not in {"rss", "lemmyCommunity"}:
            logger.warning(f"Expected outline of type `rss` or `lemmyCommunity`, not `{outline.type}`. "
                           f"Trying to parse anyway.")

        try:
            return LemmyCommunity.from_url(outline.html_url)
        except ValueError:
            logger.warning(f"Could not infer Lemmy community from outline htmlUrl attribute `{outline.html_url}`.")
        except AttributeError:
            logger.warning(f"Could not infer Lemmy community from outline htmlUrl attribute (no such attribute).")

        try:
            return LemmyCommunity.from_feed_url(outline.xml_url)
        except ValueError:
            logger.warning(f"Could not infer Lemmy community from outline xmlUrl attribute `{outline.xml_url}`.")
        except AttributeError:
            logger.warning(f"Could not infer Lemmy community from outline xmlUrl attribute (no such attribute).")

        raise ValueError("Could not infer Lemmy community from Outline object.")

    @staticmethod
    def from_opml(opml: OPML) -> tuple[list["LemmyCommunity"], int]:
        """Generate a list of :class:`LemmyCommunity` objects from an :class:`OPML` object. Traverses the OPML body
        recursively, descending a level when an outline with type "category" is encountered and trying to create a
        LemmyCommunity from any other outline element.

        :returns: A 2-tuple containing [0] the list of LemmyCommunity objects, and [1] the number of non-"category"
            outline elements that could not be successfully parsed.
        """
        communities = []
        failed = 0
        for outline in _walk_outlines(opml.body):
            try:
                communities.append(LemmyCommunity.from_outline(outline))
            except ValueError:
                failed += 1
        return communities, failed

    def _resolve_sort_by(self, sort_by_str: Optional[str] = None) -> Optional[str]:
        """Convert one of the "normalised" options for sorting posts to a string that can be used in generating URLs."""
        if sort_by_str is None:
            return None
        try:
            ambiguous_sort_by = SORT_BY_VALUES[sort_by_str]
        except KeyError:
            raise ValueError(f"Value '{sort_by_str}' not in {set(SORT_BY_VALUES.keys())}")
        if self.is_kbin:
            actual_sort_by = ambiguous_sort_by.kbin
        else:
            actual_sort_by = ambiguous_sort_by.lemmy
        if actual_sort_by is None:
            raise ValueError(f"Sorting by '{sort_by_str}' not supported for {'Kbin' if self.is_kbin else 'Lemmy'} "
                             f"communities.")
        return actual_sort_by

    def html_url(self, sort_by: Optional[str] = None):
        """Generate the HTML URL for the community. This should also be the `actor_id` that is used to identify the
        community in the Lemmy API.

        :param sort_by: How posts should be sorted.
        """
        try:
            sort_by = self._resolve_sort_by(sort_by)
        except ValueError:
            logging.warning(f"Sorting by \'{sort_by}\' not supported for community '{self.name}' at instance "
                            f"'{self.instance}'. Ignoring sort_by argument.")
            sort_by = None

        q = ""
        if self.is_kbin:
            path = f"/m/{self.name}"
            if sort_by is not None:
                path += f"/{sort_by}"
        else:
            path = f"/c/{self.name}"
            if sort_by is not None:
                q = urlencode({"sort": sort_by})
        return urlunsplit((
            "https",
            self.instance,
            path,
            q,
            ""
        ))

    def rss_url(self, sort_by: Optional[str] = None):
        """Generate the RSS feed URL for the community.

        :param sort_by: How items should be sorted in the RSS feed.
        """
        if sort_by is not None:
            if self.is_kbin:
                logging.warning(f"Sorting in RSS feed URLs not supported for Kbin communities. "
                                f"Ignoring sort_by argument.")
            else:
                try:
                    sort_by = self._resolve_sort_by(sort_by)
                except ValueError:
                    logging.warning(f"Sorting by \'{sort_by}\' not supported for community '{self.name}' at instance "
                                    f"'{self.instance}'. Ignoring sort_by argument.")
                    sort_by = None
        q = ""
        if self.is_kbin:
            path = f"/rss"
            q = urlencode({"magazine": self.name})
        else:
            path = f"/feeds/c/{self.name}.xml"
            if sort_by is not None:
                q = urlencode({"sort": sort_by})
        return urlunsplit((
            "https",
            self.instance,
            path,
            q,
            ""
        ))

    @property
    def text(self) -> str:
        """Textual representation of the community, in the form `!<community>@<instance>`."""
        return f"!{self.name}@{self.instance}"

    def to_outline(self, sort_by: Optional[str] = None, include_description: bool = False) -> Outline:
        return Outline(
            type="rss",
            text=self.text,
            title=self.title,
            description=self.description if include_description else None,
            xml_url=self.rss_url(sort_by=sort_by),
            html_url=self.html_url(sort_by=sort_by)
        )


class LemmyClient:
    API_VERSION = "v3"

    def __init__(self, site_url: str):
        self.site_url = to_https(site_url)
        self._parsed = urlsplit(self.site_url)
        self.instance = self._parsed.netloc
        self.username = None
        self.auth_token = None

    @property
    def base_api_url(self) -> str:
        return f"{self.site_url}/api/{self.API_VERSION}"

    @property
    def user_reference(self) -> str:
        """A reference to the user in the form `@<user>@<instance>`"""
        return f"@{self.username}@{self.instance}"

    @property
    def user_url(self) -> str:
        """The URL for the user's profile on Lemmy."""
        return urlunsplit((
            self._parsed.scheme,
            self._parsed.netloc,
            f"/u/{self.username}",
            self._parsed.query,
            self._parsed.fragment
        ))

    def login(self, username: str, password: str):
        self.username = username
        cred = {
            "username_or_email": username,
            "password": password
        }

        try:
            r = requests.request("POST", url=f"{self.base_api_url}/user/login", json=cred)
            r.raise_for_status()
            self.auth_token = r.json()["jwt"]
        except Exception as e:
            logger.error(f"Could not log in as user {username} on site {self.site_url}: {e}")
            raise e

    def resolve_community(self, community: Union[LemmyCommunity, str]) -> LemmyCommunity:
        """Resolve a community (get full details, including community ID).

        :param community: The community to resolve, as a :class:`LemmyCommunity` object or a URL string.
        :return: A :class:`LemmyCommunity` instance with all available data included, including the community ID.
        """
        if not self.auth_token:
            raise NotLoggedInError("No auth token found; you need to log in first.")

        if isinstance(community, LemmyCommunity):
            url = community.html_url()
        else:
            url = community

        payload = {
            "q": url,
            "auth": self.auth_token
        }

        try:
            r = requests.request("GET", url=f"{self.base_api_url}/resolve_object", params=payload)
            r.raise_for_status()
            return LemmyCommunity.from_dict(r.json()["community"]["community"])
        except Exception as e:
            logger.error(f"Could not resolve community at {url}: {e}")
            raise e

    def subscribe(self, community: LemmyCommunity):
        """Subscribe to the given Lemmy community,"""
        if not self.auth_token:
            raise NotLoggedInError("No auth token found; you need to log in first.")
        if community.id is None:
            community = self.resolve_community(community)
        cid = community.id
        payload = {
            "community_id": cid,
            "follow": True,
            "auth": self.auth_token
        }
        try:
            r = requests.request("POST", url=f"{self.base_api_url}/community/follow", json=payload)
            r.raise_for_status()
        except Exception as e:
            logger.error(f"Could not subscribe to community at {community.html_url()}: {e}")
            raise e

    @property
    def subscribed_communities(self) -> list[LemmyCommunity]:
        """The user's subscribed communities."""
        if not self.auth_token:
            raise NotLoggedInError("No auth token found; you need to log in first.")

        payload = {
            "auth": self.auth_token,
        }

        try:
            r = requests.request("GET", url=f"{self.base_api_url}/site", params=payload)
            r.raise_for_status()
            follows = r.json()["my_user"]["follows"]
            communities = []
            for c in follows:
                community = c["community"]
                try:
                    communities.append(LemmyCommunity.from_dict(community))
                except Exception as e:
                    logging.error(f"Skipping community due to error: {community['actor_id']}: {e}")
            return communities

        except Exception as e:
            logger.error(f"Could not fetch subscribed communities for user {self.username} on site {self.site_url}:"
                         f" {e}")
            raise e

    def subscribed_to_opml(
            self,
            categories: bool = False,
            sort_by: Optional[str] = None,
            title: Optional[str] = None,
            include_owner_name: bool = False,
            include_owner_id: bool = False,
            include_timestamp: bool = True
    ) -> tuple[OPML, int]:
        """Generate an OPML object representing the user's subscribed communities.

        :param categories: Whether to categories the communities by instance.
        :param sort_by: How posts in communities should be sorted (used to generate the relevant URLs).
        :param title: The OPML document's title.
        :param include_owner_name: Whether to include a reference to the user as the `ownerName` element in the OPML.
        :param include_owner_id: Whether to include a link to the user's profile as the `ownerId` element in the OPML.
        :param include_timestamp: Whether to include the current date and time as the `dateCreated` element in the OPML.

        :returns: A 2-tuple containing [0] the :class:`OPML` object, and [1] the number of exported communities.
        """
        if not self.auth_token:
            raise NotLoggedInError("No auth token found; you need to log in first.")

        communities = self.subscribed_communities
        doc = OPML()
        head = Head()
        if title:
            head.title = title
        if include_owner_name:
            head.owner_name = self.user_reference
        if include_owner_id:
            head.owner_id = self.user_url
        if include_timestamp:
            head.date_created = format_datetime(datetime.now())
        if head.title or head.owner_name or head.owner_id or head.date_created:
            doc.head = head
        body = doc.body
        cat_outlines = {}
        for c in communities:
            if categories:
                if c.instance in cat_outlines:
                    parent = cat_outlines[c.instance]
                else:
                    parent = Outline(type="category", text=c.instance)
                    cat_outlines[c.instance] = parent
            else:
                parent = body
            parent.outlines.append(c.to_outline(sort_by))
        if categories:
            for c in cat_outlines:
                body.outlines.append(cat_outlines[c])
        return doc, len(communities)


def _get_pass(ns: Namespace) -> str:
    """Get the user's password. First check if the password has been provided as a command line argument. If not, check
    if a path to a password file has been provided. If not, ask the user.
    """
    if ns.password:
        return ns.password
    elif ns.pass_file:
        with open(ns.pass_file, 'r') as f:
            return f.read().strip()
    else:
        return getpass()


def _logged_on_client(ns: Namespace) -> LemmyClient:
    """Create a :class:`LemmyClient` instance, attempt to log in and return the logged in client."""
    client = LemmyClient(ns.instance)
    client.login(ns.username, _get_pass(ns))
    return client


def export_communities(ns: Namespace):
    client = _logged_on_client(ns)

    opml, exported = client.subscribed_to_opml(
        ns.categories,
        ns.sort_by,
        ns.title,
        ns.include_user_name,
        ns.include_user_url,
        ns.include_date
    )
    xml = prettify_xml(opml.to_xml())

    if (not ns.overwrite) and os.path.exists(ns.outfile):
        logger.error(f"File already exists and --overwrite argument not supplied: {ns.outfile}")
        raise FileExistsError(ns.outfile)
    with open(ns.outfile, 'w') as f:
        f.write(xml)
        logging.info(f"Exported {exported} communities to {ns.outfile}.")


def import_communities(ns: Namespace):
    client = _logged_on_client(ns)
    with open(ns.infile, "r") as f:
        opml = OPML.from_xml(f.read())
    communities, parse_fails = LemmyCommunity.from_opml(opml)
    logger.info(f"Parsed {len(communities)} communities from {ns.infile} ({parse_fails} failures).")
    n_subscribed = 0
    n_failed = 0
    for c in communities:
        try:
            client.subscribe(c)
            logger.info(f"Subscribed to {c.text}.")
            n_subscribed += 1
        except:
            logger.error(f"Could not subscribe to {c.text}. Check logs for further details.")
            n_failed += 1
        # Rate limiting
        sleep(0.5)
    logger.info(f"Subscribed to {n_subscribed} communities; {n_failed} failures.")


def get_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="lemmy2opml.py",
        description="Export/import Lemmy community subscriptions"
    )

    parser.add_argument("--password", help="The password used to log in.")
    parser.add_argument("--pass-file", metavar="PATH",
                        help="A file containing the password used to log in (and nothing else).")
    parser.add_argument("--debug", action="store_true", help="More verbose logging.")
    parser.add_argument("--log-file", metavar="PATH", help="Where to store logs. Default prints logs to stderr.")
    parser.set_defaults(func=lambda ns: parser.print_usage())

    subparsers = parser.add_subparsers()
    im_parser = subparsers.add_parser("import", help="Import Lemmy subscriptions to your account.")
    im_parser.add_argument("instance", help="The Lemmy instance at which the user is registered.")
    im_parser.add_argument("username", help="The username used to log in.")

    im_parser.add_argument("infile", metavar="PATH", help="The file from which to import subscriptions.")
    im_parser.set_defaults(func=import_communities)

    ex_parser = subparsers.add_parser("export", help="Export your Lemmy subscriptions to a file.")
    ex_parser.add_argument("instance", help="The Lemmy instance at which the user is registered.")
    ex_parser.add_argument("username", help="The username used to log in.")

    ex_parser.add_argument("outfile", metavar="PATH", help="Where to save the file.")
    ex_parser.add_argument("-s", "--sort-by", choices=SORT_BY_VALUES.keys(),
                           help="How to sort posts when viewing communities (used to construct URLs).")
    ex_parser.add_argument("-t", "--title", help="Title to include in the OPML file.")
    ex_parser.add_argument("-c", "--categories", action="store_true", help="Categorise communities by instance.")
    ex_parser.add_argument("-n", "--include-user-name", action="store_true",
                           help="Include the Lemmy user's name in the OPML file.")
    ex_parser.add_argument("-u", "--include-user-url", action="store_true",
                           help="Include a link to the Lemmy user's profile in the OPML file.")
    ex_parser.add_argument("-d", "--include-date", action="store_true",
                           help="Include the current date and time in the OPML file.")
    ex_parser.add_argument("-w", "--overwrite", action="store_true",
                           help="Overwrite outfile if it already exists. If this argument is not provided, an error "
                                "will be raised if a file already exists at the given location.")
    ex_parser.set_defaults(func=export_communities)

    return parser


def main():
    parser = get_parser()
    ns = parser.parse_args()
    if ns.debug:
        logger.setLevel(logging.DEBUG)
    if ns.log_file:
        logger.addHandler(logging.FileHandler(ns.log_file))
    else:
        logger.addHandler(logging.StreamHandler())
    try:
        ns.func(ns)
        sys.exit(0)
    except Exception as e:
        logging.critical(f"Encountered an error (logs may provide further information): {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

import datetime as dt
import html
import logging
import os
import sys
import time
import typing as tp
import urllib.parse as urlparse
from contextlib import contextmanager

import requests

# TODO: tests
# TODO: refactoring
# TODO: /help
# TODO: /start
# TODO error handling
# TODO: github caching
# TODO: integration test
# TODO: remove magic constants


OFFSET_PATH = '/tmp/github_trending_last_update'
DEFAULT_API_TIMEOUT = 5  # seconds
DEFAULT_AGE_IN_DAYS = 7
HELP_TEXT = '/show [DAYS] - show trending repositories created in the last DAYS'


class Error(Exception):
    """Base exception class."""


class InvalidConfig(Error):
    pass


class ApiError(Error):
    """Base exception class for api interactions."""


class GithubApiError(ApiError):
    pass


class TelegramApiError(ApiError):
    pass


class InvalidCommand(Error):
    pass


class ParseError(InvalidCommand):
    pass


class Config:
    def __init__(self, github_token: str, telegram_token: str):
        self.github_token = github_token
        self.telegram_token = telegram_token


class Message:
    def __init__(self, chat_id: int, message_id: int, text: str):
        self.chat_id = chat_id
        self.message_id = message_id
        self.text = text


class Update:
    def __init__(self, update_id: int, message: Message):
        self.update_id = update_id
        self.message = message


class Repo:
    def __init__(self, name: str, description: str, html_url: str):
        self.name = name
        self.description = description
        self.html_url = html_url


class GithubApi:
    def __init__(self, token: str, timeout=DEFAULT_API_TIMEOUT) -> None:
        self.token = token
        self.timeout = timeout

    def find_trending_repositories(self, created_after: dt.datetime, limit: int) -> tp.List[Repo]:
        """
        :raises GithubApiError:
        """
        headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',
        }
        created_after_str = created_after.replace(microsecond=0).isoformat()
        url = 'https://api.github.com/search/repositories'
        params = {
            'q': f'created:>{created_after_str}',
            'sort': 'stars',
            'order': 'desc',
            'per_page': str(limit),
        }
        logging.info('getting trending repositories from github: %r with params %r', url, params)
        with _convert_exceptions(requests.RequestException, GithubApiError):
            response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        try:
            response_data = response.json()
        except ValueError as exc:
            raise GithubApiError(f"can't convert {response.text!r} to json") from exc
        items = _get_or_raise(response_data, 'items', list, GithubApiError)
        return [
            _make_repo_from_api_item(one_item)
            for one_item in items
            ]


def _make_repo_from_api_item(item) -> Repo:
    """
    :raises GithubApiError:
    """
    return Repo(
        name=_get_or_raise(item, 'name', str, GithubApiError),
        description=_get_or_raise(item, 'description', str, GithubApiError),
        html_url=_get_or_raise(item, 'html_url', str, GithubApiError),
    )


def _get_or_raise(item, key, expected_type, exception_class):
    try:
        value = item[key]
    except KeyError:
        raise exception_class(f'{item!r} misses required key {key!r}')
    else:
        if not isinstance(value, expected_type):
            raise exception_class(f'key {key!r} should be {expected_type}, got {value!r} instead')
        return value


def find_trending_repositories(github_token: str, age_in_days: int) -> tp.List[Repo]:
    """
    :raises GithubApiError:
    """
    created_after = dt.datetime.utcnow() - dt.timedelta(days=age_in_days)
    github_api = GithubApi(github_token)
    return github_api.find_trending_repositories(
        created_after=created_after,
        limit=10,
    )


def _configure_logging():
    logging.basicConfig(
        format='%(levelname)s %(message)s %(filename)s:%(lineno)s',
        level=logging.INFO,
    )


def main(offset_state=None):
    if offset_state is None:
        offset_state = FileOffsetState(OFFSET_PATH)
    _configure_logging()
    config = _get_config_or_exit(os.environ)
    telegram_api = TelegramApi(config.telegram_token)
    commands = {
        '/help': lambda _: HELP_TEXT,
        '/start': lambda _: HELP_TEXT,
        '/echo': lambda args: '\n'.join(args),
        '/show': GithubShowCommand(config.github_token),
    }
    commands_executor = CommandsExecutor(commands)
    while True:
        try:
            updates = telegram_api.get_updates(offset=offset_state.offset, limit=5, timeout=1000)
        except TelegramApiError:
            logging.error('could not get updates from telegram, sleeping 10 seconds ...', exc_info=True)
            time.sleep(10)
            continue

        if updates:
            for update in updates:
                if update.message is None:
                    parsed_message = ParsedMessage(
                        '/help',
                        [],
                    )
                else:
                    try:
                        parsed_message = parse_message_text(update.message.text)
                    except ParseError:
                        parsed_message = ParsedMessage(
                            '/echo',
                            args=['oops, something went wrong'],
                        )
                try:
                    message_text = commands_executor.execute(parsed_message)
                except Error:
                    logging.error(f'got an error when executing {parsed_message!r}')
                    message_text = 'oops, something went wrong'
                try:
                    telegram_api.send_message(
                        chat_id=update.message.chat_id,
                        text=message_text,
                        parse_mode='HTML',
                        disable_web_page_preview=True,
                        disable_notification=True,
                    )
                except TelegramApiError:
                    logging.error('could not get send message to telegram, sleeping 10 seconds ...', exc_info=True)
                    time.sleep(10)

            offset_state.offset = _get_next_offset(updates)


def _get_next_offset(bot_updates: tp.List[Update]) -> int:
    return max(update.update_id for update in bot_updates) + 1


class FileOffsetState:
    def __init__(self, path):
        self.path = path

    @property
    def offset(self) -> int:
        with open(self.path, 'r') as fileobj:
            return int(fileobj.read())

    @offset.setter
    def offset(self, offset: int):
        with open(self.path, 'w') as fileobj:
            fileobj.write(str(offset))


@contextmanager
def _convert_exceptions(from_exception_class, to_exception_class):
    try:
        yield
    except from_exception_class as exc:
        raise to_exception_class from exc


def _get_or_invalid_config(environment: tp.Mapping[str, str], key: str) -> str:
    """
    :raises InvalidConfig: When `key` is missing from `environment`.
    """
    try:
        return environment[key]
    except KeyError:
        raise InvalidConfig(f'{key} is missing from environment')


def _get_config_or_exit(environment: tp.Mapping[str, str]) -> Config:
    try:
        return get_config(environment)
    except InvalidConfig as exc:
        logging.error("invalid config: %s", exc)
        sys.exit(1)


def get_config(environment: tp.Mapping[str, str]) -> Config:
    """
    :raises InvalidConfig: When either 'GITHUB_TOKEN' or 'TELEGRAM_TOKEN' are missing
    """
    github_token = _get_or_invalid_config(environment, 'GITHUB_TOKEN')
    telegram_token = _get_or_invalid_config(environment, 'TELEGRAM_TOKEN')
    return Config(
        github_token=github_token,
        telegram_token=telegram_token,
    )


def format_html_message(repositories: tp.List[Repo]) -> str:
    message_parts = []
    for repo in repositories:
        part = f'<a href="{html.escape(repo.html_url)}">{html.escape(repo.name)}</a> - {html.escape(repo.description)}'
        message_parts.append(part)
    return '\n\n'.join(message_parts)


class TelegramApi:
    def __init__(self, token: str, timeout: int = DEFAULT_API_TIMEOUT) -> None:
        self.token = token
        self.timeout = timeout

    def send_message(self, chat_id: int, text: str, parse_mode: str = '', disable_web_page_preview: bool = False,
                     disable_notification: bool = False) -> None:
        """
        :raises TelegramApiError:
        """
        if not text:
            return
        url = self._get_method_url('sendMessage')
        params = {
            'chat_id': chat_id,
            'text': text,
            'disable_web_page_preview': disable_web_page_preview,
            'disable_notification': disable_notification,
        }
        if parse_mode:
            params['parse_mode'] = parse_mode

        logging.info('sending message to chat_id %s with params %r', chat_id, params)
        with _convert_exceptions(requests.RequestException, TelegramApiError):
            response = requests.post(url, json=params, timeout=self.timeout)
            response.raise_for_status()

    def get_updates(self, offset: int, limit: int, timeout: int) -> tp.List[Update]:
        """
        :raises TelegramApiError:
        """
        url = self._get_method_url('getUpdates')
        params = dict(
            offset=offset,
            timeout=timeout,
            limit=limit,
        )
        logging.info('getting updates from telegram ...')
        with _convert_exceptions(requests.RequestException, TelegramApiError):
            response = requests.post(url, json=params, timeout=self.timeout)
            response.raise_for_status()
        # TODO: dry with github
        try:
            response_data = response.json()
        except ValueError as exc:
            raise TelegramApiError(f"can't convert {response.text!r} to json") from exc
        result = _get_or_raise(response_data, 'result', list, TelegramApiError)
        logging.info('got response %s', response_data)
        updates = [
            _make_update_from_api_item(item)
            for item in result
            ]
        logging.info('got %d updates from telegram', len(updates))
        return updates

    def _get_method_url(self, method_name: str) -> str:
        return urlparse.urljoin(
            f'https://api.telegram.org/bot{self.token}/',
            method_name,
        )


def _is_message(update_item: tp.Mapping) -> bool:
    try:
        _get_or_raise(update_item, 'message', dict, ValueError)
    except ValueError:
        return False
    else:
        return True


def _make_update_from_api_item(item: tp.Mapping) -> Update:
    """
    :raises TelegramApiError:
    """
    update_id = _get_or_raise(item, 'update_id', int, TelegramApiError)
    try:
        message = _make_message_from_api_item(item)
    except ValueError:
        logging.error("can't parse %r into message", item)
        message = None
    return Update(
        update_id=update_id,
        message=message,
    )


def _make_message_from_api_item(item: tp.Mapping) -> tp.Union[Message, None]:
    """
    :raises ValueError: When can't parse item as a Message
    """
    if not _is_message(item):
        return None
    message_item = _get_or_raise(item, 'message', dict, ValueError)
    chat_item = _get_or_raise(message_item, 'chat', dict, ValueError)
    if 'text' not in message_item:
        text = ''
    else:
        text = _get_or_raise(message_item, 'text', str, ValueError)
    return Message(
        chat_id=_get_or_raise(chat_item, 'id', int, ValueError),
        message_id=_get_or_raise(message_item, 'message_id', int, ValueError),
        text=text,
    )


class ParsedMessage:
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def __repr__(self):
        return f'ParsedMessage(name={self.name!r}, args={self.args!r})'


def parse_message_text(text: str) -> ParsedMessage:
    """
    :raises ParseError:
    """
    if not text:
        raise ParseError
    splitted = text.split(' ')
    return ParsedMessage(
        name=splitted[0],
        args=splitted[1:],
    )


class CommandsExecutor:
    def __init__(self, commands_by_name: tp.Mapping) -> None:
        self.commands_by_name = commands_by_name

    def execute(self, parsed_message: ParsedMessage) -> str:
        command = self.commands_by_name[parsed_message.name]
        return command(parsed_message.args)


class GithubShowCommand:
    def __init__(self, token, default_age_in_days=DEFAULT_AGE_IN_DAYS):
        self.token = token
        self.default_age_in_days = default_age_in_days

    def __call__(self, args):
        """
        :raises GithubApiError:
        """
        age_in_days = self._get_age_in_days_or_invalid_args(args)
        repositories = find_trending_repositories(self.token, age_in_days)
        return format_html_message(repositories)

    def _get_age_in_days_or_invalid_args(self, args):
        if not args:
            return self.default_age_in_days
        if len(args) != 1:
            raise InvalidCommand(f'this command accepts only one argument, got {len(args)}')
        try:
            return int(args[0])
        except ValueError:
            raise InvalidCommand(f'{args[0]} should be an integer')

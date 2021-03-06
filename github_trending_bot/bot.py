import datetime as dt
import html
import logging
import os
import sys
import time
import typing as tp
import urllib.parse as urlparse
from contextlib import contextmanager

from cachetools.func import ttl_cache
import requests

HELP_COMMAND = '/help'
START_COMMAND = '/start'
SHOW_COMMAND = '/show'
ECHO_COMMAND = '/echo'
TIMESTAMP_COMMAND = '/timestamp'

OFFSET_PATH = '/var/lib/github_trending_bot/last_update'

GITHUB_API_BASE = 'https://api.github.com'
DEFAULT_GITHUB_API_SOCKET_TIMEOUT = 5  # seconds
GITHUB_CACHE_TTL = 600  # seconds
DEFAULT_AGE_IN_DAYS = 7

DEFAULT_TELEGRAM_API_SOCKET_TIMEOUT = 70  # seconds
DEFAULT_TELEGRAM_API_LONG_POLLING_TIMEOUT = 60  # seconds
TELEGRAM_UPDATES_LIMIT = 5  # items in an array
HELP_TEXT = '\n\n'.join([
    f'{SHOW_COMMAND} [DAYS] - show trending repositories created in the last DAYS',
    f'{TIMESTAMP_COMMAND} [%Y-%m-%dT%H:%M:%S] - convert UTC date string to Unix timestamp',
])

STAR_SYMBOL = '\u2605'


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
    def __init__(self, name: str, description: str, html_url: str, language: tp.Optional[str], stargazers_count: int):
        self.name = name
        self.description = description
        self.html_url = html_url
        self.language = language
        self.stargazers_count = stargazers_count


class ParsedMessage:
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def __repr__(self):
        return f'ParsedMessage(name={self.name!r}, args={self.args!r})'


class CommandsExecutor:
    def __init__(self, commands_by_name: tp.Mapping) -> None:
        self.commands_by_name = commands_by_name

    def execute(self, parsed_message: ParsedMessage) -> str:
        try:
            command = self.commands_by_name[parsed_message.name]
        except KeyError:
            raise InvalidCommand(f'unknown command {parsed_message.name}, type `/help`')
        else:
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


class TimestampCommand:
    _USAGE_STRING = 'usage: /timestamp %Y-%m-%dT%H:%M:%S'

    def __call__(self, args):
        self._validate_args(args)
        if not args:
            naive_d_time = dt.datetime.utcnow()
        else:
            date_string = args[0]
            try:
                naive_d_time = dt.datetime.strptime(date_string, '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                raise InvalidCommand(TimestampCommand._USAGE_STRING)
        return str(int(naive_d_time.replace(tzinfo=dt.timezone.utc).timestamp()))

    def _validate_args(self, args):
        if len(args) > 1:
            raise InvalidCommand(f'too many arguments, {TimestampCommand._USAGE_STRING}')


class GithubApi:
    def __init__(self, token: str, socket_timeout=DEFAULT_GITHUB_API_SOCKET_TIMEOUT) -> None:
        self.token = token
        self.socket_timeout = socket_timeout

    def find_trending_repositories(self, created_after: dt.datetime, limit: int) -> tp.List[Repo]:
        """
        :raises GithubApiError:
        """
        headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',
        }
        created_after_str = created_after.replace(microsecond=0).isoformat()
        url = urlparse.urljoin(GITHUB_API_BASE, '/search/repositories')
        params = {
            'q': f'created:>{created_after_str}',
            'sort': 'stars',
            'order': 'desc',
            'per_page': str(limit),
        }
        logging.info('getting trending repositories from github: %r with params %r', url, params)
        with _convert_exceptions(requests.RequestException, GithubApiError):
            response = requests.get(url, params=params, headers=headers, timeout=self.socket_timeout)
            response.raise_for_status()
        try:
            response_data = response.json()
        except ValueError as exc:
            raise GithubApiError(f"can't convert {response.text!r} to json") from exc
        items = _get_or_raise(response_data, 'items', list, GithubApiError)
        logging.info('got %d repositories from github', len(items))
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
        description=(_get_or_raise(item, 'description', (str, type(None)), GithubApiError)) or '',
        html_url=_get_or_raise(item, 'html_url', str, GithubApiError),
        language=_get_or_raise(item, 'language', (str, type(None)), GithubApiError),
        stargazers_count=_get_or_raise(item, 'stargazers_count', int, GithubApiError),
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


@ttl_cache(ttl=GITHUB_CACHE_TTL)
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
    commands_executor = _get_commands_executor(config)
    while True:
        try:
            updates = telegram_api.get_updates(
                offset=offset_state.offset,
                limit=TELEGRAM_UPDATES_LIMIT,
                timeout=DEFAULT_TELEGRAM_API_LONG_POLLING_TIMEOUT,
            )
        except TelegramApiError:
            logging.error('could not get updates from telegram, sleeping 10 seconds ...', exc_info=True)
            time.sleep(10)
            continue

        for update in updates:
            if update.message is None:
                logging.info('update %r has no message', update.update_id)
                continue
            parsed_message = _get_parsed_message(update)
            try:
                message_text = commands_executor.execute(parsed_message)
            except InvalidCommand as exc:
                message_text = str(exc)
            except Error:
                logging.error(f'got an error when executing {parsed_message!r}', exc_info=True)
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
        offset_state.offset = _get_next_offset(offset_state, updates)


def _get_commands_executor(config: Config) -> CommandsExecutor:
    commands = {
        HELP_COMMAND: lambda _: HELP_TEXT,
        START_COMMAND: lambda _: HELP_TEXT,
        ECHO_COMMAND: lambda args: '\n'.join(args),
        SHOW_COMMAND: GithubShowCommand(config.github_token),
        TIMESTAMP_COMMAND: TimestampCommand(),
    }
    return CommandsExecutor(commands)


def _get_parsed_message(update: Update) -> ParsedMessage:
    if update.message is None:
        parsed_message = ParsedMessage(
            HELP_COMMAND,
            [],
        )
    else:
        try:
            parsed_message = parse_message_text(update.message.text)
        except ParseError:
            parsed_message = ParsedMessage(
                ECHO_COMMAND,
                args=['oops, something went wrong'],
            )
    return parsed_message


class FileOffsetState:
    def __init__(self, path: str):
        self.path = path

    @property
    def offset(self) -> int:
        with open(self.path, 'r') as fileobj:
            return int(fileobj.read())

    @offset.setter
    def offset(self, offset: int):
        with open(self.path, 'w') as fileobj:
            fileobj.write(str(offset))


def _get_next_offset(offset_state: FileOffsetState, bot_updates: tp.List[Update]) -> int:
    if not bot_updates:
        return offset_state.offset
    return max(update.update_id for update in bot_updates) + 1


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
        if repo.language is not None:
            language_part = f'{html.escape(repo.language)} '
        else:
            language_part = ''
        part += f' [{language_part}{repo.stargazers_count}{STAR_SYMBOL}]'
        message_parts.append(part)
    return '\n\n'.join(message_parts)


class TelegramApi:
    def __init__(self, token: str, socket_timeout: int = DEFAULT_TELEGRAM_API_SOCKET_TIMEOUT) -> None:
        self.token = token
        self.socket_timeout = socket_timeout

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

        logging.info('sending message to chat_id %s with params %r ...', chat_id, params)
        with _convert_exceptions(requests.RequestException, TelegramApiError):
            response = requests.post(url, json=params, timeout=self.socket_timeout)
            response.raise_for_status()
        logging.info('sent message to chat_id %s with params %r', chat_id, params)

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
            response = requests.post(url, json=params, timeout=self.socket_timeout)
            response.raise_for_status()
        try:
            response_data = response.json()
        except ValueError as exc:
            raise TelegramApiError(f"can't convert {response.text!r} to json") from exc
        logging.info('got response %s', response_data)
        result = _get_or_raise(response_data, 'result', list, TelegramApiError)
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

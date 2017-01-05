import datetime as dt
import logging
import os
import sys
import typing as tp

import requests

# TODO: refactoring & tests, /help, /start, error handling, github caching

PATH = '/tmp/github_trending_last_update'

logging.basicConfig(
    format='%(levelname)s %(message)s %(filename)s:%(lineno)s',
    level=logging.INFO,
)


class Error(Exception):
    """Base exception class."""


class InvalidConfig(Error):
    pass


class Config:
    def __init__(self, github_token, telegram_token):
        self.github_token = github_token
        self.telegram_token = telegram_token


class Update:
    def __init__(self, telegram_id: int, chat_id: int, message_id: int, age_in_days: int):
        self.telegram_id = telegram_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.age_in_days = age_in_days


def _get_age_in_days(item):
    command, *args = item['message']['text'].split()
    if len(args) != 1:
        return 7
    try:
        return int(args[0])
    except ValueError:
        return 7


class Bot:
    def __init__(self, telegram_token: str):
        assert telegram_token
        self.telegram_token = telegram_token

    def get_updates(self, offset: int, limit: int, timeout: int) -> tp.List[Update]:
        url = f'https://api.telegram.org/bot{self.telegram_token}/getUpdates'
        params = dict(
            offset=offset,
            timeout=timeout,
            limit=limit,
        )
        logging.info('getting updates from telegram ...')
        response = requests.post(url, json=params)
        response.raise_for_status()
        logging.info('got response %s', response.json())
        updates = [
            Update(
                telegram_id=item['update_id'],
                chat_id=item['message']['chat']['id'],
                message_id=item['message']['message_id'],
                age_in_days=_get_age_in_days(item),
            )
            for item in response.json()['result']
            if item.get('message', {}).get('text', '').startswith('/show')
            ]
        logging.info('got %d updates from telegram', len(updates))
        return updates

    def reply(self, chat_id, message_id, text):
        if not text:
            return
        url = f'https://api.telegram.org/bot{self.telegram_token}/sendMessage'
        params = dict(
            chat_id=chat_id,
            text=text,
            parse_mode='HTML',
            disable_web_page_preview=True,
            disable_notification=True,
        )
        logging.info('sending reply to %s with params %r', chat_id, params)
        response = requests.post(url, json=params)
        response.raise_for_status()


class Repo:
    def __init__(self, name: str, description: str, url: str):
        self.name = name
        self.description = description
        self.url = url


class GithubApi:
    def __init__(self, token: str) -> None:
        self.token = token

    def find_trending_repositories(self, created_after: dt.datetime, limit: int) -> tp.List[Repo]:
        headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',
        }
        created_after_str = created_after.replace(microsecond=0).isoformat()
        url = (f'https://api.github.com/search/repositories?'
               f'sort=stars&order=desc&q=created:>{created_after_str}&per_page={limit}'
               )
        logging.info('getting trending repositories from github: %r', url)

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return [
            Repo(name=item['name'], description=item['description'], url=item['html_url'])
            for item in response.json()['items']
            ]


def reply_to_update(bot: Bot, update: Update, repositories: tp.List[Repo]):
    message_parts = []
    for repo in repositories:
        part = f'<a href="{repo.url}">{repo.name}</a> - {repo.description}'
        message_parts.append(part)
    message = '\n\n'.join(message_parts)
    bot.reply(update.chat_id, update.message_id, message)


def find_trending_repositories(github_token: str, age_in_days: int) -> tp.List[Repo]:
    created_after = dt.datetime.utcnow() - dt.timedelta(days=age_in_days)
    github_api = GithubApi(github_token)
    return github_api.find_trending_repositories(
        created_after=created_after,
        limit=10,
    )


def main():
    config = _get_config_or_exit(os.environ)
    bot = Bot(config.telegram_token)
    offset = _read_offset()
    while True:
        bot_updates = bot.get_updates(offset=offset, limit=5, timeout=1000)
        if bot_updates:
            for update in bot_updates:
                # TODO: use caching
                repos = find_trending_repositories(config.github_token, update.age_in_days)
                reply_to_update(bot, update, repos)
            offset = _get_next_offset(bot_updates)
            _save_offset(offset)


def _get_next_offset(bot_updates: tp.List[Update]) -> int:
    return max(update.telegram_id for update in bot_updates) + 1


def _read_offset() -> int:
    with open(PATH, 'r') as fileobj:
        return int(fileobj.read())


def _save_offset(offset: int):
    with open(PATH, 'w') as fileobj:
        fileobj.write(str(offset))


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

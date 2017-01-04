import datetime as dt
import logging
import os

import requests

PATH = '/tmp/github_trending_last_update'

logging.basicConfig(level=logging.INFO)


class Bot:
    def __init__(self, telegram_token):
        assert telegram_token
        self.telegram_token = telegram_token

    def get_updates(self, offset, limit, timeout):
        url = f'https://api.telegram.org/bot{self.telegram_token}/getUpdates'
        params = dict(
            offset=offset,
            timeout=timeout,
            limit=limit,
        )
        logging.info('getting updates from telegram')
        response = requests.post(url, json=params)
        response.raise_for_status()
        return [
            Update(item['update_id'], item['message']['chat']['id'])
            for item in response.json()['result']
            if item['message']['text'] == '/show'
            ]

    def reply(self, chat_id, text):
        url = f'https://api.telegram.org/bot{self.telegram_token}/sendMessage'
        params = dict(
            chat_id=chat_id,
            text=text,
            parse_mode='HTML',
        )
        logging.info('sending reply to %s with params %r', chat_id, params)
        response = requests.post(url, json=params)
        response.raise_for_status()


class Update:
    def __init__(self, telegram_id, chat_id):
        self.telegram_id = telegram_id
        self.chat_id = chat_id


class Repo:
    def __init__(self, name, description, url):
        self.name = name
        self.description = description
        self.url = url


def make_bot():
    return Bot(os.getenv('TELEGRAM_TOKEN'))


def reply_to_update(bot, update, repositories):
    message_parts = []
    for repo in repositories:
        part = f'<a href="{repo.url}">{repo.name}</a> - {repo.description}'
        message_parts.append(part)
    message = '\n'.join(message_parts)
    bot.reply(update.chat_id, message)


def get_trending_repos(github_token):
    headers = {
        'Authorization': f'token {github_token}',
    }
    start_from = dt.datetime.now() - dt.timedelta(days=7)
    url = (f'https://api.github.com/search/repositories?'
           f'sort=stars&order=desc&q=created:>{start_from:%Y-%m-%d}&per_page=10')
    logging.info(
        'getting trending repositories from github, headers %r, url %r', headers, url)
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return [
        Repo(name=item['name'], description=item['description'], url=item['html_url'])
        for item in response.json()['items']
        ]


def main():
    bot = make_bot()
    github_token = os.getenv('GITHUB_TOKEN')
    assert github_token
    offset = _read_offset()
    while True:
        bot_updates = bot.get_updates(offset=offset, limit=5, timeout=1000)
        if bot_updates:
            repos = get_trending_repos(github_token)
            for update in bot_updates:
                reply_to_update(bot, update, repos)
            offset = _get_next_offset(bot_updates)
            _save_offset(offset)


def _get_next_offset(bot_updates):
    return max(update.telegram_id for update in bot_updates) + 1


def _read_offset():
    try:
        with open(PATH, 'r') as fileobj:
            return int(fileobj.read())
    except FileNotFoundError:
        return 0


def _save_offset(offset):
    with open(PATH, 'w') as fileobj:
        fileobj.write(str(offset))


if __name__ == '__main__':
    main()

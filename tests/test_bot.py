import datetime as dt
import json
import os
import urllib.parse as urlparse

import pytest
import requests
import responses

from github_trending_bot import bot


def test_get_config():
    environment = {
        'GITHUB_TOKEN': 'some_github_token',
        'TELEGRAM_TOKEN': 'some_telegram_token',
    }
    config = bot.get_config(environment)
    assert config.github_token == 'some_github_token'
    assert config.telegram_token == 'some_telegram_token'


@pytest.mark.parametrize('environment', [
    # no GITHUB_TOKEN
    {'TELEGRAM_TOKEN': 'some_telegram_token'},
    # no TELEGRAM_TOKEN
    {'GITHUB_TOKEN': 'some_github_token'},
])
def test_get_config_failure(environment):
    with pytest.raises(bot.InvalidConfig):
        bot.get_config(environment)


@responses.activate
def test_github_api_find_trending_repositories():
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        json={
            'items': [
                {
                    'name': 'some_name',
                    'description': 'some_description',
                    'html_url': 'http://example.com',
                }
            ]
        }
    )
    api = bot.GithubApi('some_github_token')
    repositories = api.find_trending_repositories(
        created_after=dt.datetime(2017, 1, 5, 12, 3, 23, 686),
        limit=1,
    )
    assert len(responses.calls) == 1
    call = responses.calls[0]
    _assert_requests_call(
        call,
        expected_url='https://api.github.com/search/repositories',
        expected_params={
            'sort': 'stars',
            'order': 'desc',
            'per_page': '1',
            'q': 'created:>2017-01-05T12:03:23',
        },
        expected_headers={
            'Authorization': 'token some_github_token',
            'Accept': 'application/vnd.github.v3+json',
        },
    )
    assert len(repositories) == 1
    repo = repositories[0]
    assert repo.name == 'some_name'
    assert repo.description == 'some_description'
    assert repo.html_url == 'http://example.com'


@pytest.mark.parametrize('mock_kwargs', [
    # bad status
    {
        'status': 400,
    },
    # raises
    {
        'body': requests.Timeout(),
    },
    # not a json body
    {
        'body': 'not a json',
    },
    # bad item
    {
        'json': {
            'items': [
                {'no': 'keys'}
            ]
        }
    },
    # bad items type
    {
        'json': {
            'items': 9,
        },
    }

])
@responses.activate
def test_github_api_find_trending_repositories_error_handling(mock_kwargs):
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        **mock_kwargs
    )
    api = bot.GithubApi('some_github_token')
    with pytest.raises(bot.GithubApiError):
        api.find_trending_repositories(
            created_after=dt.datetime(2017, 1, 5, 12, 3, 23, 686),
            limit=1,
        )


def test_format_html_message():
    repositories = [
        bot.Repo(
            name='first_name',
            description='first_description',
            html_url='http://first.example.com',
        ),
        bot.Repo(
            name='<&second_name>',
            description='<&second_description>',
            html_url='http://\'second".example.com',
        ),
    ]
    actual_message = bot.format_html_message(repositories)
    expected_message = (
        '<a href="http://first.example.com">first_name</a> - first_description\n\n'
        '<a href="http://&#x27;second&quot;.example.com">&lt;&amp;second_name&gt;</a> - &lt;&amp;second_description&gt;'
    )
    assert actual_message == expected_message


@responses.activate
def test_telegram_api_send_message():
    responses.add(
        responses.POST,
        'https://api.telegram.org/botsome_telegram_token/sendMessage',
    )
    api = bot.TelegramApi('some_telegram_token')
    api.send_message(
        chat_id=99,
        text='<b>some_text</b>',
        parse_mode='HTML',
        disable_web_page_preview=True,
        disable_notification=True,
    )
    assert (len(responses.calls) == 1)
    call = responses.calls[0]
    _assert_requests_call(
        call,
        expected_url='https://api.telegram.org/botsome_telegram_token/sendMessage',
        expected_json_payload={
            'chat_id': 99,
            'text': '<b>some_text</b>',
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
            'disable_notification': True,
        }
    )


@pytest.mark.parametrize('mock_kwargs', [
    {
        'status': 400,
    },
    {
        'body': requests.Timeout(),
    },
])
@responses.activate
def test_telegram_api_send_message_bad_status(mock_kwargs):
    responses.add(
        responses.POST,
        'https://api.telegram.org/botsome_telegram_token/sendMessage',
        **mock_kwargs
    )
    api = bot.TelegramApi('some_telegram_token')
    with pytest.raises(bot.TelegramApiError):
        api.send_message(
            chat_id=99,
            text='<b>some_text</b>',
        )


def _make_message_item(update_id, chat_id, message_id, text=None):
    result = {
        'message': {
            'chat': {
                'id': chat_id
            },
            'message_id': message_id
        },
        'update_id': update_id,
    }
    if text is not None:
        result['message']['text'] = text
    return result


@pytest.mark.parametrize('message_item, expected_update_id, expected_message', [
    (
        _make_message_item(1, 2, 3, '/show'),
        1,
        bot.Message(2, 3, '/show'),
    ),
    # missing text
    (
        _make_message_item(1, 2, 3),
        1,
        bot.Message(2, 3, ''),
    ),
    # malformed message
    (
        {'update_id': 1},
        1,
        None,
    ),
])
@responses.activate
def test_telegram_api_get_updates(message_item, expected_update_id, expected_message):
    responses.add(
        responses.POST,
        'https://api.telegram.org/botsome_telegram_token/getUpdates',
        json={
            'result': [
                message_item
            ]
        },
    )
    api = bot.TelegramApi('some_telegram_token')
    updates = api.get_updates(
        offset=1,
        limit=2,
        timeout=3,
    )
    assert (len(responses.calls) == 1)
    call = responses.calls[0]
    _assert_requests_call(
        call,
        expected_url='https://api.telegram.org/botsome_telegram_token/getUpdates',
        expected_json_payload={
            'offset': 1,
            'limit': 2,
            'timeout': 3,
        }
    )
    assert len(updates) == 1
    update = updates[0]
    message = update.message
    assert update.update_id == expected_update_id
    if expected_message is not None:
        assert message.chat_id == expected_message.chat_id
        assert message.message_id == expected_message.message_id
        assert message.text == expected_message.text
    else:
        assert message is None


@pytest.mark.parametrize('mock_kwargs', [
    # requests raises
    {
        'body': requests.Timeout('mock timeout'),
    },
    # not a json
    {
        'body': 'not a json',
    },
    # bad result type
    {
        'json': {'result': 9},
    },
    # message misses required fields
    {
        'json': {
            'result': [
                {
                    'message': {},
                }
            ]
        },
    },
])
@responses.activate
def test_telegram_api_get_updates_error_handling(mock_kwargs):
    responses.add(
        responses.POST,
        'https://api.telegram.org/botsome_telegram_token/getUpdates',
        **mock_kwargs
    )
    api = bot.TelegramApi('some_telegram_token')
    with pytest.raises(bot.TelegramApiError):
        api.get_updates(
            offset=1,
            limit=2,
            timeout=3,
        )


@pytest.mark.parametrize('text, expected_name, expected_args', [
    ('/help', '/help', []),
    ('/show 1', '/show', ['1']),
])
def test_parse_message_text(text, expected_name, expected_args):
    parsed = bot.parse_message_text(text)
    assert parsed.name == expected_name
    assert parsed.args == expected_args


def test_parse_message_text_error():
    with pytest.raises(bot.ParseError):
        bot.parse_message_text('')


@pytest.mark.parametrize('name', [
    '/help',
    '/start',
])
def test_run_help_command(name):
    parsed_message = bot.ParsedMessage(
        name=name,
        args=[],
    )
    commands = bot.CommandsExecutor({
        '/help': lambda _: 'some_help_text',
        '/start': lambda _: 'some_help_text',
    })
    assert commands.execute(parsed_message) == 'some_help_text'


def _make_repo(age_in_days):
    return bot.Repo(
        name=f'some_name {age_in_days}',
        description='some_description',
        html_url='http://example.com',
    )


@pytest.mark.parametrize('args, expected_result', [
    # 7 by default
    (
        [],
        '<a href="http://example.com">some_name 7</a> - some_description',
    ),
    (
        ['1'],
        '<a href="http://example.com">some_name 1</a> - some_description',
    ),
])
def test_github_show_command(monkeypatch, args, expected_result):
    monkeypatch.setattr(
        bot,
        'find_trending_repositories',
        lambda github_token, age_in_days: [_make_repo(age_in_days)]
    )
    result = bot.GithubShowCommand('some_github_token')(args)
    assert result == expected_result


@pytest.mark.parametrize('args', [
    # many args
    ['3', '4'],
    # not an int
    [''],
])
def test_github_show_command_error_handling(args):
    with pytest.raises(bot.InvalidCommand):
        bot.GithubShowCommand('some_github_token')(args)


class _BreakFromInfiniteLoop(Exception):
    pass


class _DummyOffsetState:
    def __init__(self):
        self._offset = 0

    @property
    def offset(self):
        return self._offset

    @offset.setter
    def offset(self, offset):
        self._offset = offset
        raise _BreakFromInfiniteLoop


@pytest.mark.parametrize('update_texts, expected_text, expected_offset_state', [
    (
        [
            '/show',
        ],
        '<a href="http://example.com">some_name 7</a> - some_description',
        4,
    ),
    (
        [
            '/show 3',
        ],
        '<a href="http://example.com">some_name 3</a> - some_description',
        4,
    ),
    (
        [
            '/help',
            '/start'
        ],
        bot.HELP_TEXT,
        4,
    ),
])
def test_main(monkeypatch, update_texts, expected_text, expected_offset_state):
    updates = []
    for text in update_texts:
        message = bot.Message(
            chat_id=1,
            message_id=2,
            text=text,
        )
        update = bot.Update(
            update_id=3,
            message=message,
        )
        updates.append(update)
    sent_messages = _monkeypatch_for_main(monkeypatch, updates)
    offset_state = _DummyOffsetState()
    with pytest.raises(_BreakFromInfiniteLoop):
        bot.main(offset_state=offset_state)
    assert len(sent_messages) == len(updates)
    for (update, (args, kwargs)) in zip(updates, sent_messages):
        assert kwargs['chat_id'] == update.message.chat_id
        assert kwargs['text'] == expected_text
        assert kwargs['parse_mode'] == 'HTML'
        assert kwargs['disable_web_page_preview']
        assert kwargs['disable_notification']
    assert offset_state.offset == expected_offset_state


def _monkeypatch_for_main(monkeypatch, updates):
    sent_messages = []
    monkeypatch.setattr(
        bot,
        'find_trending_repositories',
        lambda github_token, age_in_days: [_make_repo(age_in_days)]
    )
    monkeypatch.setattr(
        os,
        'environ',
        {
            'GITHUB_TOKEN': 'some_github_token',
            'TELEGRAM_TOKEN': 'some_telegram_token',
        }
    )

    monkeypatch.setattr(
        bot.TelegramApi,
        'get_updates',
        lambda self, offset, limit, timeout: updates
    )
    monkeypatch.setattr(
        bot.TelegramApi,
        'send_message',
        lambda self, *args, **kwargs: sent_messages.append((args, kwargs))
    )
    return sent_messages


def _get_http_get_params(parse_result):
    return dict(urlparse.parse_qsl(parse_result.query))


def _assert_requests_call(
    call, expected_url=None, expected_params=None, expected_headers=None, expected_json_payload=None):
    parse_result = urlparse.urlparse(call.request.url)
    if expected_url is not None:
        actual_url = f'{parse_result.scheme}://{parse_result.netloc}{parse_result.path}'
        assert actual_url == expected_url
    if expected_params is not None:
        actual_params = _get_http_get_params(parse_result)
        assert actual_params == expected_params
    if expected_headers is not None:
        assert expected_headers.items() < call.request.headers.items()  # is subset
    if expected_json_payload is not None:
        actual_json_payload = json.loads(call.request.body)
        assert actual_json_payload == expected_json_payload

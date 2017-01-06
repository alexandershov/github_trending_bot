import datetime as dt
import json
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


@responses.activate
def test_telegram_api_get_messages():
    responses.add(
        responses.POST,
        'https://api.telegram.org/botsome_telegram_token/getUpdates',
        json={
            'result': [
                _make_message_item(1, 2, 3, '/show'),
                # missing text is legal
                _make_message_item(4, 5, 6),
            ]
        },
    )
    api = bot.TelegramApi('some_telegram_token')
    messages = api.get_messages(
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
    assert len(messages) == 2
    first = messages[0]
    assert first.update_id == 1
    assert first.chat_id == 2
    assert first.message_id == 3
    assert first.text == '/show'
    second = messages[1]
    assert second.update_id == 4
    assert second.chat_id == 5
    assert second.message_id == 6
    assert second.text == ''


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
                    'message': {}
                }
            ]
            ,
        },
    },
])
@responses.activate
def test_telegram_api_get_messages_error_handling(mock_kwargs):
    responses.add(
        responses.POST,
        'https://api.telegram.org/botsome_telegram_token/getUpdates',
        **mock_kwargs
    )
    api = bot.TelegramApi('some_telegram_token')
    with pytest.raises(bot.TelegramApiError):
        api.get_messages(
            offset=1,
            limit=2,
            timeout=3,
        )


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

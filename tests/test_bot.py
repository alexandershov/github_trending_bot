import datetime as dt
import urllib.parse as urlparse

import pytest
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


def _make_repo_item(name, description, html_url):
    return {
        'name': name,
        'description': description,
        'html_url': html_url,
    }


@responses.activate
def test_github_api_find_trending_repositories():
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        json={
            'items': [
                _make_repo_item('some_name', 'some_description', 'http://example.com'),
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
    assert repo.url == 'http://example.com'


@responses.activate
def test_github_api_find_trending_repositories_bad_status():
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        status=400,
    )
    api = bot.GithubApi('some_github_token')
    with pytest.raises(bot.GithubApiError):
        api.find_trending_repositories(
            created_after=dt.datetime(2017, 1, 5, 12, 3, 23, 686),
            limit=1,
        )


@responses.activate
def test_github_api_find_trending_repositories_invalid_response():
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        body='not a json'
    )
    api = bot.GithubApi('some_github_token')
    with pytest.raises(bot.GithubApiError):
        api.find_trending_repositories(
            created_after=dt.datetime(2017, 1, 5, 12, 3, 23, 686),
            limit=1,
        )


@responses.activate
def test_github_api_find_trending_repositories_invalid_response():
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        json={
            'items': [
                {'no': 'keys'}
            ]
        },
    )
    api = bot.GithubApi('some_github_token')
    with pytest.raises(bot.GithubApiError):
        api.find_trending_repositories(
            created_after=dt.datetime(2017, 1, 5, 12, 3, 23, 686),
            limit=1,
        )


def _get_http_get_params(parse_result):
    return dict(urlparse.parse_qsl(parse_result.query))


def _assert_requests_call(call, expected_url, expected_params, expected_headers):
    parse_result = urlparse.urlparse(call.request.url)
    actual_url = f'{parse_result.scheme}://{parse_result.netloc}{parse_result.path}'
    assert actual_url == expected_url
    actual_params = _get_http_get_params(parse_result)
    assert actual_params == expected_params
    assert expected_headers.items() < call.request.headers.items()  # is subset

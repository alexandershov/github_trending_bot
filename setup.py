from setuptools import find_packages, setup

setup(
    name='github_trending_bot',
    version='0.1.0',
    install_requires=[
        'requests==2.12.4',
    ],
    entry_points={
        'console_scripts': [
            'github_trending_bot = github_trending_bot.bot:main',
        ]
    },
    tests_require=[
        'pytest==3.0.5',
    ],
    packages=find_packages(),
)

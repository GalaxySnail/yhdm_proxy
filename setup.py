import os
from setuptools import setup

assert os.path.exists("pyproject.toml")

# for __version__
with open("src/yhdm_proxy/__version__.py", encoding="utf-8") as f:
    exec(f.read())  # pylint: disable=W0122

with open("README.md", encoding="utf-8") as f:
    readme = f.read()


setup(
    name="yhdm-proxy",
    version=__version__,  # type: ignore[name-defined]  # pylint: disable=E0602
    url="https://github.com/GalaxySnail/yhdm_proxy",
    author="GalaxySnail",
    description="一个用于下载樱花动漫 m3u8 文件的反向代理",
    long_description=readme,
    license="AGPL-3.0-or-later",
    package_dir={"": "src"},
    install_requires=[
        "trio >= 0.20.0",
        "httpx >= 0.22.0",
        "h11 >= 0.12.0",
    ],
    package_data={"yhdm_proxy": ["py.typed"]},
    python_requires=">=3.7",
    keywords=["trio", "proxy", "樱花动漫", "m3u8"],
    classifiers = [
        "Development Status :: 4 - Beta",
        "Framework :: Trio",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Natural Language :: Chinese (Simplified)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Internet :: Proxy Servers",
        "Topic :: Internet :: WWW/HTTP :: HTTP Servers",
        "Topic :: Multimedia :: Video :: Conversion",
        "Typing :: Typed",
    ],
)

#!/bin/bash
# This bash script builds the API documentation for ConfigArgParse.

# Change to the source directory path.
cd -P "$( dirname "${BASH_SOURCE}" )"

# Stop if errors
set -euo pipefail
IFS=$'\n\t,'

# Figure the project version
project_version="$(setuptools-git-versioning)"

# Figure commit ref or tag
git_sha="$(git rev-parse HEAD)"
if ! git describe --exact-match --tags > /dev/null 2>&1 ; then
    is_tag=false
else
    git_sha="$(git describe --exact-match --tags)"
    is_tag=true
fi

# Init output folder
docs_folder="./apidocs/"
rm -rf "${docs_folder}"
mkdir -vp "${docs_folder}"

# We generate the docs for the argparse module too, such that we can document
# the methods inherited from argparse.ArgumentParser, not only the methods that configargparse overrides.
# And it help to have a better vision of the whole thing also.
curl https://raw.githubusercontent.com/python/cpython/3.13/Lib/argparse.py > ./argparse.py
echo "__docformat__ = 'restructuredtext'" >> ./argparse.py
# Delete the file when the script exits
trap "rm -f ./argparse.py" EXIT

set +e
pydoctor \
    --project-name="ConfigArgParse ${project_version}" \
    --project-url="https://github.com/bw2/ConfigArgParse" \
    --html-viewsource-base="https://github.com/bw2/ConfigArgParse/tree/${git_sha}" \
    --intersphinx=https://docs.python.org/3/objects.inv \
    --make-html \
    --quiet \
    --project-base-dir=.\
    --docformat=google \
    --html-output="${docs_folder}" \
    ./argparse.py ./configargparse.py

if [ "$?" = 0 ] ; then
    printf '\n%s\n' "API docs generated in ${docs_folder}"
elif [ -e "${docs_folder}/index.html" ] ; then
    printf '\n%s\n' "API docs generated in ${docs_folder} with errors"
fi


#!/bin/bash
# This bash script builds the API documentation for ConfigArgParse.

# Resolve source directory path. From https://stackoverflow.com/questions/59895/how-to-get-the-source-directory-of-a-bash-script-from-within-the-script-itself/246128#246128
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd $SCRIPT_DIR

# Stop if errors
set -euo pipefail
IFS=$'\n\t,'

# Figure the project version
project_version="$(python3 setup.py -V)"

# Figure commit ref
git_sha="$(git rev-parse HEAD)"
if git describe --exact-match --tags > /dev/null 2>&1 ; then
    git_sha="$(git describe --exact-match --tags)"
fi

# Init output folder
docs_folder="./apidocs/"
rm -rf "${docs_folder}"
mkdir -p "${docs_folder}"

# We generate the docs for the argparse module too, such that we can document 
# the methods inherited from argparse.ArgumentParser, not only the methods that configargparse overrides.
# And it help to have a better vision of the whole thing also.
# Delete the file when the script exits
trap "rm -f ./argparse.py" EXIT
# Download it from a fixed release and check it is that release's file, since
# pydoctor renders its docstrings into the published pages
cpython_release_tag="v3.9.25"
curl -fsSL "https://raw.githubusercontent.com/python/cpython/${cpython_release_tag}/Lib/argparse.py" > ./argparse.py
echo '1f549e2f7f896f884a9de998a05d7dadb930594262a43d59517b06ff3ee6fc51  argparse.py' | shasum -a 256 -c -
echo "__docformat__ = 'restructuredtext'" >> ./argparse.py

pydoctor \
    --project-name="ConfigArgParse ${project_version}" \
    --project-url="https://github.com/bw2/ConfigArgParse" \
    --html-viewsource-base="https://github.com/bw2/ConfigArgParse/tree/${git_sha}" \
    --template-dir=./apidocs_templates \
    --intersphinx=https://docs.python.org/3/objects.inv \
    --make-html \
    --quiet \
    --project-base-dir=.\
    --docformat=google \
    --html-output="${docs_folder}" \
    ./argparse.py ./configargparse.py || [ $? -eq 2 ]
# pydoctor exits with 2 when some docstrings can't be parsed, which happens in
# the downloaded argparse.py, after it has written the docs. Any other failure
# stops this script, and so does a run that wrote no docs.
test -f "${docs_folder}index.html"

# pydoctor links each object to its source under --html-viewsource-base, but
# argparse.py isn't in this repository, so point its links at the file in the
# CPython release it came from. Only a line was added at its end above, so the
# line numbers still match.
sed -i.bak "s#https://github.com/bw2/ConfigArgParse/tree/${git_sha}/argparse.py#https://github.com/python/cpython/blob/${cpython_release_tag}/Lib/argparse.py#g" "${docs_folder}"*.html
rm -f "${docs_folder}"*.html.bak

echo "API docs generated in ${docs_folder}"

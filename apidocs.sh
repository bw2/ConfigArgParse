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
if ! git describe --exact-match --tags > /dev/null 2>&1 ; then
    is_tag=false
else
    git_sha="$(git describe --exact-match --tags)"
    is_tag=true
fi

# Init output folder
docs_folder="./apidocs/"
rm -rf "${docs_folder}"
mkdir -p "${docs_folder}"

# We generate the docs for the argparse module too, such that we can document 
# the methods inherited from argparse.ArgumentParser, not only the methods that configargparse overrides.
# And it help to have a better vision of the whole thing also.
curl https://raw.githubusercontent.com/python/cpython/3.9/Lib/argparse.py > ./argparse.py
echo "__docformat__ = 'restructuredtext'" >> ./argparse.py
# Delete the file when the script exits
trap "rm -f ./argparse.py" EXIT

# Run pydoctor build
pydoctor \
    --project-name="ConfigArgParse ${project_version}" \
    --project-url="https://github.com/bw2/ConfigArgParse" \
    --html-viewsource-base="https://github.com/bw2/ConfigArgParse/tree/${git_sha}" \
    --make-html \
    --quiet \
    --project-base-dir=.\
    --docformat=google \
    --html-output="${docs_folder}" \
    ./argparse.py ./configargparse.py || true 
    
# There is currently an error 'Unknown target name: "foo".' in 
# configargparse.ArgumentParser.__init__ that makes the build fails.
# But ther than that, it looks good.

echo "API docs generated in ${docs_folder}"
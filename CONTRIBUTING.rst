Contributing to ConfigArgParse
------------------------------

What you can do 
~~~~~~~~~~~~~~~

If you like the project and think you could help with making it better, there are many ways you can do it:

- Create an issue to report a bug or suggest a new feature
- Triage old issues that need a refresh
- Implement fixes for existing issues
- Help with improving the documentation
- Spread the word about the project to your colleagues, friends, blogs or any other channels
- Any other things you could imagine

Any contribution would be of great help and we'll highly appreciate it! 
If you have any questions, please create a new issue.

Development process
~~~~~~~~~~~~~~~~~~~

Create a fork of the git repository and checkout a new branch from master branch. 
The branch name may start with an associated issue number so that we can easily cross-reference them. 
For example, use ``1234-some-brach-name`` as the name of the branch working to fix issue 1234. 
Once your changes are ready and are passing the automated tests, open a pull request.

Don’t forget to sync your fork once in a while to work from the latest revision.

Pre-commit checks
~~~~~~~~~~~~~~~~~

- Run unit tests: ``pytest``. 

Review process 
~~~~~~~~~~~~~~

- Code changes and code added should have tests: untested code is buggy code and should
  not be accepted by reviewers.
- All code changes must be reviewed by at least one maintainer who is not an author 
  of the code being added.
- When a reviewer completes a review, they should always say what the next step(s) should be: 
  - Ok we can merge the patch as is 
  - Some optional changes requested
  - Some required changes are requested
  - An issue should be opened to tackle another problem discovered during the coding process.
- If a substantial change is pushed after a review, a follow-up review should be done. 
  Small changes and nit-picks do not required follow-up reviews.

Release process 
~~~~~~~~~~~~~~~

The following is a high level overview and might not match the current state of things.

- Create a branch: name it with release dash the name of the new version, i.e. ``release-1.5.1``
- On the branch, update the version and release notes.
- Create a PR for that branch, wait for tests to pass and get an approval.
- At this point, the *owner* should checkout the release branch, 
  created and push a new tag named after the version i.e. ``1.5.1``, 
  this will trigger the PyPi release process, monitor the process in the GitHub action UI...
- Update the version and append ``.dev0`` to the current 
  version number. In this way, stable versions only exist for a brief period of time 
  (if someone tries to do a pip install from the git source, they will get a ``.dev0`` 
  version instead of a misleading stable version number)
- Wait for tests to pass and merge PR


---

owner: people with administrative rights on the repo, only they are able to push a new tag.
maintainers: people with push rights, they can merge PR if the requirements are met. 
# Publishing to PyPI

This project uses [UV](https://docs.astral.sh/uv/) as the package manager and GitHub Actions for automated publishing to PyPI.

## Prerequisites

1. **PyPI Account**: Create an account at [https://pypi.org/](https://pypi.org/)
2. **Trusted Publishing**: Configure trusted publishing (no API tokens needed!) at [https://pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/)
   - Add a new publisher with:
     - PyPI Project Name: `kitbag`
     - Owner: `l0kifs`
     - Repository name: `kitbag`
     - Workflow name: `publish-to-pypi.yml`
     - Environment name: (leave blank)

## Automated Publishing (Recommended)

The project is configured to automatically publish to PyPI when a new GitHub release is created.

**Check current release version** before starting:
```bash
gh release list --limit 10 2>&1 | cat
```

1. **Update version** in `pyproject.toml` (the single source of truth for the version):
   ```toml
   version = "0.2.0"  # Update to your new version
   ```

2. **Update CHANGELOG.md** (required): move entries from `[Unreleased]` into a new version section and update the compare links at the bottom.

3. **Commit and push** your changes:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 0.2.0"
   git push
   ```

4. **Create a GitHub release**:

   Using GitHub CLI with inline notes:
   ```bash
   # Create the release
   gh release create v0.2.0 \
     --title "v0.2.0 - Release Title" \
     --notes "## 🎯 New Features
   - Feature 1 description
   - Feature 2 description

   ## 🐛 Bug Fixes
   - Fix 1 description

   ## 📚 Documentation
   - Doc updates

   ## 🔗 Full Changelog
   See [CHANGELOG.md](https://github.com/l0kifs/kitbag/blob/v0.2.0/CHANGELOG.md)"
   ```

   Or using the GitHub web interface:
   - Go to [https://github.com/l0kifs/kitbag/releases/new](https://github.com/l0kifs/kitbag/releases/new)
   - Create a new tag (e.g., `v0.2.0`)
   - Add release title and description
   - Click "Publish release"

   To verify the release:
   ```bash
   gh release view v0.2.0
   ```

5. **GitHub Actions will automatically**:
   - Build the package using UV
   - Publish to PyPI using trusted publishing
   - You can monitor the progress in the Actions tab

## Manual Publishing

If you need to publish manually:

1. **Install UV** (if not already installed):
   ```bash
   pip install uv
   ```

2. **Build the package**:
   ```bash
   uv build
   ```
   This creates distribution files in the `dist/` directory.

3. **Publish using UV** (requires PyPI API token):
   ```bash
   uv publish
   ```
   Or use `twine`:
   ```bash
   pip install twine
   twine upload dist/*
   ```

## Testing on TestPyPI

Before publishing to the main PyPI, you can test on TestPyPI:

1. Configure trusted publishing for TestPyPI at [https://test.pypi.org/manage/account/publishing/](https://test.pypi.org/manage/account/publishing/)

2. Manually trigger the workflow or publish directly to TestPyPI:
   ```bash
   uv publish --index-url https://test.pypi.org/legacy/
   ```

3. Test installation:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ kitbag
   ```

## Best Practices

1. **Always create tags on the `main` branch** - Never tag on `develop` or feature branches
2. **Merge develop to main before tagging** - Ensure all changes are in main
3. **Test on TestPyPI first** (optional but recommended for major releases)
4. **Use semantic versioning** (MAJOR.MINOR.PATCH)
5. **Analyze changes** in the repository between the last release and current state
6. **Update CHANGELOG.md** with all changes before release (required)
7. **Test build locally** before pushing tags
8. **Keep credentials secure** - use project-specific tokens
9. **Test installation** from PyPI after publishing
10. **Create a GitHub Release** after a successful publish
11. **Monitor PyPI stats** and user feedback

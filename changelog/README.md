# Cross-Repository Changelog

This directory tracks changes made in this repository that have dependencies or impact on other Tabsy repositories:
- **Backend**: https://github.com/dhairya9925/tabsy-backend
- **Frontend**: https://github.com/dhairya9925/tabsy-frontend
- **Mobile**: https://github.com/dhairya9925/tabsy-mobile

## Workflow

1. When making a change in this repository that requires a corresponding change in another repo (e.g., adding or modifying an API endpoint, changing auth token format, altering schema):
2. Copy `TEMPLATE.md` to a new file named `YYYY-MM-DD_<short-description>.md` (e.g., `2026-09-19_add-invite-endpoint.md`).
3. Fill out the details, including which other repos are affected and specific action items.
4. Set status to `⬜ Pending`.
5. Once the dependent changes are implemented and verified in the target repositories, mark status as `✅ Done`.

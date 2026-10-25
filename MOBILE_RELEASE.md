# Mobile backend release procedure

Each staging and production deployment has its own runtime environment. Set
`MOBILE_APP_MIN_VERSION`, `MOBILE_APP_RECOMMENDED_VERSION`,
`MOBILE_APP_STORE_URL_IOS`, `MOBILE_APP_STORE_URL_ANDROID`, and
`MOBILE_API_COMPATIBILITY` in that deployment's runtime configuration. Do not
copy staging values into production. Versions must use `MAJOR.MINOR.PATCH`;
the recommended version may be blank or equal to or greater than the minimum.
Store links may be blank and otherwise must use HTTPS.

## Release order

1. Keep `MOBILE_API_COMPATIBILITY` unchanged for backend changes compatible with
   the currently supported native runtime. Deploy and verify staging first.
2. For an incompatible API change, release a client native runtime that supports
   the new API. Update `MOBILE_API_COMPATIBILITY` with that runtime and raise
   `MOBILE_APP_MIN_VERSION` in the same production rollout. Keep the previous
   backend behavior available until the new store build is available to users.
3. Set `MOBILE_APP_RECOMMENDED_VERSION` and the published platform store links
   after store availability is confirmed. The recommendation can be blank
   when no optional update should be shown.
4. Roll out organization mobile features through the operator delivery API.
   The mobile policy response reports only allowlisted enabled features. The
   backend checks those same settings when serving protected mobile operations;
   disabling a feature blocks the next request immediately.

## Recovery

App Store and Google Play binaries that were already published cannot be
recalled from user devices by the backend. Recover by deploying a backend change
compatible with the published binary or by publishing a corrected store build.
Lowering `MOBILE_APP_MIN_VERSION` only changes server policy; it does not remove
an installed binary. OTA channels and native runtime configuration belong to
the mobile repository. Native code changes require a new store build; OTA may
only deliver changes compatible with the installed native runtime.

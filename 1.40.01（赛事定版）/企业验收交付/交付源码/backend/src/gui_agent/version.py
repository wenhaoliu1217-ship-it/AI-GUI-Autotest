APP_VERSION = "1.40.01"

# The supplied release contains only a compiled frontend bundle. Keep its
# independently observed version explicit until the React/TypeScript source
# and lockfile are recovered and a new bundle can be built reproducibly.
FRONTEND_BUNDLE_VERSION = "1.40.01"

# The independently versioned HTTP/JSON contract lets a certified prebuilt
# frontend run with a newer backend patch without pretending their release
# versions are identical. The launcher rejects a bundle that does not declare
# this exact contract before the service starts.
API_CONTRACT_VERSION = "ai-gui-http-v1"

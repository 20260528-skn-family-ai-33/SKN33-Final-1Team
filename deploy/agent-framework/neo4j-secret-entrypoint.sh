#!/bin/bash
set -eu
export NEO4J_AUTH="neo4j/$(cat /run/secrets/neo4j_password)"
exec /startup/docker-entrypoint.sh "$@"

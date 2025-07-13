#!/bin/bash

function print_help() {
  echo "Usage: $(basename "${0}") <ACTION>"
  echo
  echo "Small wrapper that enables me easily run this project locally"
  echo
  echo "ACTION:"
  echo " - docker:  Rebuild & start this project as in a docker container"
  echo " - run:     Start this project directly"
  echo
}


case "${1}" in

  docker)
    echo "Running as a DOCKER"
    docker build --build-arg RELEASE_VERSION="TEST" -t archivetube .
    docker rm archivetube_test
    docker run \
      --network=host --name archivetube_test -it \
      --volume /etc/localtime:/etc/localtime:ro \
      --volume ./config/:/archivetube/config \
      --volume ./downloads:/archivetube/downloads \
      archivetube
    ;;

  run)
    if [[ ! -d .venv ]]; then
      echo "No .venv directory found, did you forget to create it?"
      echo "Hint:    python -m venv .venv"
      exit 1
    fi
    source .venv/bin/activate
    export verbose_logs=true
    gunicorn src.ArchiveTube:app -c gunicorn_config.py
    ;;

  -h|--help) print_help ;;
  *)
    echo "Unknown/unhandled ACTION: '${1}'"
    echo
    print_help
    exit 1
    ;;
esac
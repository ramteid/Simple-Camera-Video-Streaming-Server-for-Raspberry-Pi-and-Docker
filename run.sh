export COMPOSE_DOCKER_CLI_BUILD=1
export DOCKER_BUILDKIT=1

#docker-compose down
docker-compose build
docker-compose up -d --force-recreate
docker logs -f cam_camera_1
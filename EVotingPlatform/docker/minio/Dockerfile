# REQ-05: MinIO built from its public source at a pinned release.
#
# MinIO no longer publishes official Docker images, and a third-party image
# would put an unknown publisher in the audit-log trust chain. Building from
# source pins exactly what runs: the release tag AND its commit hash, so the
# build fails if the tag is ever moved to different code.
FROM golang:1.24.8-bookworm AS build

ARG MINIO_RELEASE=RELEASE.2025-10-15T17-29-55Z
ARG MINIO_COMMIT=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a

RUN git clone --depth 1 --branch "${MINIO_RELEASE}" https://github.com/minio/minio.git /src \
 && cd /src \
 && test "$(git rev-parse HEAD)" = "${MINIO_COMMIT}" \
 && LDFLAGS="$(go run buildscripts/gen-ldflags.go)" \
 && CGO_ENABLED=0 go build -trimpath -tags kqueue -ldflags "-s -w ${LDFLAGS}" -o /out/minio .

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --home /data minio \
 && mkdir -p /data && chown minio /data
COPY --from=build /out/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9090
HEALTHCHECK --interval=10s --timeout=5s --retries=6 \
    CMD curl -fs http://localhost:9000/minio/health/live || exit 1
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9090"]

FROM golang:1.26-alpine AS build
# Supplied by compose. The build context carries no usable VCS data, so the
# release identity is passed in rather than derived here.
ARG PORTAL_VERSION=dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN LDFLAGS="-s -w -X github.com/neuralyze/valheim-portal/internal/version.Version=${PORTAL_VERSION}" \
 && CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="$LDFLAGS" -o /out/valheim-portal ./cmd/valheim-portal \
 && CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags="$LDFLAGS" -o /out/map-tile-builder ./cmd/map-tile-builder

FROM alpine:3.22
RUN addgroup -S portal && adduser -S -G portal -h /var/lib/valheim-portal portal
COPY --from=build /out/valheim-portal /usr/local/bin/valheim-portal
COPY --from=build /out/map-tile-builder /usr/local/bin/map-tile-builder
USER portal
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/valheim-portal"]

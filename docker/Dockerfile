FROM bmeares/meerschaum

RUN mrsm install plugin compose
COPY --chown=meerschaum:meerschaum ./ /app
WORKDIR /app
RUN mrsm compose init -y

ENTRYPOINT ["/app/docker/bootstrap.sh"]

FROM eclipse-temurin:21.0.9_10-jre-alpine@sha256:08eecc477dbe3f2e33daac27f36e41daf7f4ec51d2f3396006e54fa41832c74c

LABEL authors="MaxKey <support@maxsso.net>"

RUN apk add --no-cache curl \
    && addgroup -S maxkey \
    && adduser -S -G maxkey maxkey
WORKDIR /maxkey-mgt

ARG JAR_FILE=build/libs/maxkey-mgt-boot-*-ga.jar
COPY --chown=maxkey:maxkey ${JAR_FILE} maxkey-mgt-boot.jar

USER maxkey
EXPOSE 9526
ENV TZ=Asia/Shanghai
ENV JAVA_OPTS="-Xms512m -Xmx2048m -Djava.security.egd=file:/dev/./urandom"
ENTRYPOINT ["sh", "-c", "exec java ${JAVA_OPTS} -jar maxkey-mgt-boot.jar"]
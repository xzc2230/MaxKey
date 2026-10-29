FROM node:24-alpine
WORKDIR /app
COPY bridge.mjs ./
ENV NODE_ENV=production \
    BIND_HOST=0.0.0.0 \
    PORT=3011
EXPOSE 3011
CMD ["node", "bridge.mjs"]

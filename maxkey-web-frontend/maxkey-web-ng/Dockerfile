#MaxKey Frontend Docker Build

FROM nginx:1.27.5-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10

LABEL authors="MaxKey <support@maxsso.net>"

COPY ./nginx.conf /etc/nginx/conf.d/default.conf

#RUN rm -rf /usr/share/nginx/html/*

COPY  dist/browser /usr/share/nginx/html/maxkey

#CMD ["nginx", "-g", "daemon off;"]

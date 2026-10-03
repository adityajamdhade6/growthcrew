# GrowthCrew web app. The API address is fixed at build time (Next.js rewrites).
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG GROWTHCREW_API_URL=http://api:8000
ARG NEXT_PUBLIC_DEMO_EMAIL=
ARG NEXT_PUBLIC_DEMO_PASSWORD=
ENV GROWTHCREW_API_URL=$GROWTHCREW_API_URL \
    NEXT_PUBLIC_DEMO_EMAIL=$NEXT_PUBLIC_DEMO_EMAIL \
    NEXT_PUBLIC_DEMO_PASSWORD=$NEXT_PUBLIC_DEMO_PASSWORD
RUN npm run build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production
COPY --from=build /web ./
EXPOSE 3000
CMD ["npm", "run", "start"]

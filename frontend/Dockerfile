FROM node:24-slim
WORKDIR /app

# Install dependencies
COPY package.json package-lock.json ./
RUN npm ci

# Copy project
COPY . .

CMD ["npm", "run", "dev"]

/**
 * Titan Cloud Gateway — Pino Logger Factory
 */

"use strict";

const pino = require("pino");

const level = process.env.LOG_LEVEL || "info";

function createLogger(name) {
  return pino({
    name: `titan:${name}`,
    level,
    transport:
      process.env.NODE_ENV !== "production"
        ? {
            target: "pino-pretty",
            options: { colorize: true, translateTime: "HH:MM:ss" },
          }
        : undefined,
  });
}

module.exports = { createLogger };

#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { defineWebApp } from '../lib/meridian-web-app';
import { loadDotenv } from '../lib/meridian-web-stack';

defineWebApp(new App(), { dotenv: loadDotenv(), processEnv: process.env });

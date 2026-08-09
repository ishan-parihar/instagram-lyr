---
name: instagram-lyr
description: Instagram automation with profile access, posts, reels, stories, DMs, and analytics. Use this skill whenever the user requests Instagram operations, social media posting, content creation, or any Instagram-related tasks. Triggers on phrases like "instagram post", "instagram story", "instagram search", "instagram automation", "social media posting", "content creation", "instagram reels", "get instagram profile", "search instagram", or any request for Instagram functionality.
---

# Instagram-lyr Skill

This skill enables AI agents to interact with Instagram using the instagram-lyr CLI tool. It provides comprehensive Instagram automation including profile access, posts, reels, stories, DMs, and analytics.

## Prerequisites

- instagram-lyr CLI must be installed globally on the system
- The CLI must be accessible in the system PATH
- Browser cookies are required for authenticated operations

## Command Structure

### Available Commands

```bash
# Session management
instagram-lyr                          # Start MCP server
instagram-lyr --login                  # Import browser cookies
instagram-lyr --status                 # Check session status
instagram-lyr --list-tools             # List available MCP tools

# Content commands
instagram-lyr get_user_profile --username natgeo --sections posts
instagram-lyr get_user_posts --username natgeo --limit 10
instagram-lyr get_user_reels --username natgeo --limit 5
instagram-lyr get_user_stories --username natgeo
instagram-lyr search_users --query "travel photography"
instagram-lyr search_hashtags --query "sunset"
```

## MCP Tool Usage

The instagram-lyr CLI serves as an MCP server with the following tools:

- `get_user_profile` - Get profile info with optional sections
- `get_user_posts` - Get structured post data
- `get_user_reels` - Get reels with engagement metrics
- `get_user_stories` - Get active stories
- `search_users` - Search for users
- `search_hashtags` - Search hashtags
- `send_dm` - Send a direct message
- `like_post` - Like a post or reel
- `comment_on_post` - Post a comment
- `get_business_insights` - Get reach, impressions, engagement
- `get_audience_insights` - Get audience demographics
- `get_content_insights` - Get content performance data
- `transcribe_reel` - Transcribe a reel to SRT
- `analyze_reel_with_gemini` - AI reel analysis with Gemini

## MCP Configuration

```json
{
  "mcpServers": {
    "instagram": {
      "command": "uvx",
      "args": ["instagram-lyr"]
    }
  }
}
```

## Authentication

Set `INSTAGRAM_COOKIES` env var or run `--login` for browser-based auth:

```bash
instagram-lyr --login
```

## Session Integration

Install session hooks for ambient context:

```bash
instagram-lyr --install-hook
```

This shows Instagram session state on every agent session start.

## Workflow

### Step 1: Analyze User Request
- Determine the type of Instagram operation needed (profile, posts, reels, stories, DMs)
- Identify if authentication is required
- Select appropriate command based on user intent

### Step 2: Generate Command
- Use the command structure above to build the appropriate CLI command
- Add relevant flags for sections, limits, and output format
- Include any required parameters (usernames, hashtags, post IDs, etc.)

### Step 3: Execute and Validate
- Run the command using shell execution
- Check for successful completion
- Parse the output in the appropriate format
- Report the result to the user

### Step 4: Handle Errors
- If authentication fails: guide user to run `instagram-lyr --login`
- If command not found: suggest installing instagram-lyr
- If rate limited: suggest waiting before retrying
- If invalid parameters: provide correct usage examples

## Error Handling

### Common Issues and Solutions

1. **Command not found**
   - Error: "instagram-lyr: command not found"
   - Solution: Install instagram-lyr globally or add to PATH

2. **Authentication errors**
   - Error: "Authentication required"
   - Solution: Run `instagram-lyr --login` to import browser cookies

3. **Rate limiting**
   - Error: "Rate limit exceeded"
   - Solution: Wait before retrying the operation

4. **Invalid parameters**
   - Error: "Invalid username/post ID"
   - Solution: Verify the username or post ID is correct

## Integration Example

### User Request: "Get National Geographic's recent posts"

**Skill Processing:**
1. Identify as a profile operation
2. Generate command: `instagram-lyr get_user_posts --username natgeo --limit 10`
3. Execute and parse results
4. Report posts to user

### User Request: "Search for sunset photography hashtags"

**Skill Processing:**
1. Identify as a search operation
2. Generate command: `instagram-lyr search_hashtags --query "sunset photography"`
3. Execute and format results
4. Report hashtags to user

### User Request: "Send a DM to a user"

**Skill Processing:**
1. Identify as a write operation requiring authentication
2. Generate command: `instagram-lyr send_dm --username <user> --text "Message"`
3. Execute and report results

## Best Practices

1. **Always check authentication requirements** before executing write operations
2. **Use appropriate sections** when getting profiles (posts, reels, stories, etc.)
3. **Handle rate limiting gracefully** by suggesting delays between operations
4. **Provide context in results** when returning content data (author, timestamp, metrics)
5. **Use reasonable limits** when browsing to avoid overwhelming results
6. **Handle errors with actionable suggestions** for resolution
7. **Verify usernames** before posting or DMing to ensure they exist
"""
Gmail Actions for Browser Use
Defines agent actions for Gmail integration including 2FA code retrieval,
email reading, and authentication management.
"""

import logging
import os

from pydantic import BaseModel, Field

from browser_use.agent.views import ActionResult
from browser_use.tools.service import Tools

from .service import GmailService

logger = logging.getLogger(__name__)

# Global Gmail service instance - initialized when actions are registered
_gmail_service: GmailService | None = None


class GetRecentEmailsParams(BaseModel):
	"""Parameters for getting recent emails"""

	keyword: str = Field(default='', description='A single keyword for search, e.g. github, airbnb, etc.')
	max_results: int = Field(default=3, ge=1, le=50, description='Maximum number of emails to retrieve (1-50, default: 3)')
	time_filter: str = Field(default='5m', description='Gmail time filter such as 5m, 1h, 1d, or 7d.')


class SendGmailEmailParams(BaseModel):
	"""Parameters for sending an email"""

	to: str = Field(description='Recipient email address. Use comma-separated addresses for multiple recipients.')
	subject: str = Field(description='Email subject.')
	body: str = Field(description='Plain text email body.')
	cc: str = Field(default='', description='Optional comma-separated CC recipients.')
	bcc: str = Field(default='', description='Optional comma-separated BCC recipients.')


def register_gmail_actions(
	tools: Tools,
	gmail_service: GmailService | None = None,
	access_token: str | None = None,
	token_file: str | None = None,
) -> Tools:
	"""
	Register Gmail actions with the provided tools
	Args:
	    tools: The browser-use tools to register actions with
	    gmail_service: Optional pre-configured Gmail service instance
	    access_token: Optional direct access token (alternative to file-based auth)
	    token_file: Optional OAuth token JSON path. Defaults to GMAIL_OAUTH_TOKEN_PATH or browser-use config.
	"""
	global _gmail_service

	# Use provided service or create a new one with access token if provided
	if gmail_service:
		_gmail_service = gmail_service
	elif access_token:
		_gmail_service = GmailService(access_token=access_token)
	else:
		_gmail_service = GmailService(token_file=token_file)

	@tools.registry.action(
		description='Get the configured Gmail address to use for account signups, login, or verification emails.',
	)
	async def get_gmail_address() -> ActionResult:
		email = os.getenv('GMAIL_USER_EMAIL', '').strip() or os.getenv('GMAIL_ADDRESS', '').strip()
		if not email:
			return ActionResult(
				error='GMAIL_USER_EMAIL is not configured.',
				long_term_memory='Gmail address is not configured',
			)
		return ActionResult(
			extracted_content=(
				f'Use this email address when an email address is needed: {email}\n'
				'If a site sends a verification code or magic link to this address, use get_recent_emails with a relevant keyword to read it.'
			),
			long_term_memory=f'Configured Gmail address is {email}',
		)

	@tools.registry.action(
		description='Get recent emails from the mailbox with a keyword to retrieve verification codes, OTP, 2FA tokens, magic links, or any recent email content. Keep your query a single keyword.',
		param_model=GetRecentEmailsParams,
	)
	async def get_recent_emails(params: GetRecentEmailsParams) -> ActionResult:
		"""Get recent emails from the last 5 minutes with full content"""
		try:
			if _gmail_service is None:
				raise RuntimeError('Gmail service not initialized')

			# Ensure authentication
			if not _gmail_service.is_authenticated():
				logger.info('📧 Gmail not authenticated, attempting authentication...')
				authenticated = await _gmail_service.authenticate()
				if not authenticated:
					return ActionResult(
						extracted_content='Failed to authenticate with Gmail. Please ensure Gmail credentials are set up properly.',
						long_term_memory='Gmail authentication failed',
					)

			# Use specified max_results (1-50, default 3), with a bounded time filter
			max_results = params.max_results
			time_filter = params.time_filter.strip() or '5m'

			# Build query with time filter and optional user query
			query_parts = [f'newer_than:{time_filter}']
			if params.keyword.strip():
				query_parts.append(params.keyword.strip())

			query = ' '.join(query_parts)
			logger.info(f'🔍 Gmail search query: {query}')

			# Get emails
			emails = await _gmail_service.get_recent_emails(max_results=max_results, query=query, time_filter=time_filter)

			if not emails:
				query_info = f" matching '{params.keyword}'" if params.keyword.strip() else ''
				memory = f'No recent emails found from last {time_filter}{query_info}'
				return ActionResult(
					extracted_content=memory,
					long_term_memory=memory,
				)

			# Format with full email content for large display
			content = f'Found {len(emails)} recent email{"s" if len(emails) > 1 else ""} from the last {time_filter}:\n\n'

			for i, email in enumerate(emails, 1):
				content += f'Email {i}:\n'
				content += f'From: {email["from"]}\n'
				content += f'Subject: {email["subject"]}\n'
				content += f'Date: {email["date"]}\n'
				content += f'Content:\n{email["body"]}\n'
				content += '-' * 50 + '\n\n'

			logger.info(f'📧 Retrieved {len(emails)} recent emails')
			return ActionResult(
				extracted_content=content,
				include_extracted_content_only_once=True,
				long_term_memory=f'Retrieved {len(emails)} recent emails from last {time_filter} for query {query}.',
			)

		except Exception as e:
			logger.error(f'Error getting recent emails: {e}')
			return ActionResult(
				error=f'Error getting recent emails: {str(e)}',
				long_term_memory='Failed to get recent emails due to error',
			)

	@tools.registry.action(
		description='Send a plain text email with Gmail from the authenticated mailbox. Use only when the user explicitly asks to send an email or confirms the recipient and content.',
		param_model=SendGmailEmailParams,
	)
	async def send_gmail_email(params: SendGmailEmailParams) -> ActionResult:
		"""Send an email through Gmail"""
		try:
			if _gmail_service is None:
				raise RuntimeError('Gmail service not initialized')

			if not _gmail_service.is_authenticated():
				logger.info('📧 Gmail not authenticated, attempting authentication...')
				authenticated = await _gmail_service.authenticate()
				if not authenticated:
					return ActionResult(
						error='Failed to authenticate with Gmail. Check GMAIL_OAUTH_TOKEN_PATH and OAuth scopes.',
						long_term_memory='Gmail authentication failed',
					)

			result = await _gmail_service.send_email(
				to=params.to,
				subject=params.subject,
				body=params.body,
				cc=params.cc,
				bcc=params.bcc,
			)
			if result.get('error'):
				return ActionResult(
					error=f'Error sending Gmail email: {result["error"]}',
					long_term_memory='Failed to send Gmail email due to API error',
				)

			message_id = result.get('id', '')
			return ActionResult(
				extracted_content=f'Sent Gmail email to {params.to}. Message id: {message_id}',
				long_term_memory=f'Sent Gmail email to {params.to}.',
			)

		except Exception as e:
			logger.error(f'Error sending Gmail email: {e}')
			return ActionResult(
				error=f'Error sending Gmail email: {str(e)}',
				long_term_memory='Failed to send Gmail email due to error',
			)

	return tools

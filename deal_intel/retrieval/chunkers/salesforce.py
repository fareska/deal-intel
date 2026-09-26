from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.contracts.reference import Account, Contact, Opportunity
from deal_intel.permissions.gate import baseline_access_level
from deal_intel.retrieval.chunkers.base import (
    ChunkDraft,
    IngestContext,
    labelled_text,
    make_chunk,
    percent,
)
from deal_intel.retrieval.dataset import DatasetFile


def chunk_opportunities(context: IngestContext) -> list[EvidenceChunk]:
    accounts = context.reference.accounts
    return [
        make_chunk(context, opportunity_draft(opportunity, accounts[opportunity.account_id]))
        for opportunity in context.reference.opportunities.values()
    ]


def chunk_accounts(context: IngestContext) -> list[EvidenceChunk]:
    return [
        make_chunk(context, account_draft(account))
        for account in context.reference.accounts.values()
    ]


def chunk_contacts(context: IngestContext) -> list[EvidenceChunk]:
    accounts = context.reference.accounts
    return [
        make_chunk(context, contact_draft(contact, accounts[contact.account_id]))
        for contact in context.reference.contacts.values()
    ]


def opportunity_draft(opportunity: Opportunity, account: Account) -> ChunkDraft:
    text = labelled_text(
        f"Opportunity {opportunity.opportunity_id}: {opportunity.opportunity_name}",
        [
            ("Account", f"{opportunity.account_name} ({opportunity.account_id})"),
            ("Stage", opportunity.stage),
            ("Type", opportunity.type),
            ("Forecast category", opportunity.forecast_category),
            ("ACV", opportunity.acv),
            ("TCV", opportunity.tcv),
            ("Renewal term months", opportunity.renewal_term_months),
            ("Probability", percent(opportunity.probability)),
            ("Close date", opportunity.close_date),
            ("Owner", opportunity.owner),
            ("Primary competitor", opportunity.primary_competitor),
            ("Risk level", opportunity.risk_level),
            ("Approval required", opportunity.approval_required),
            ("Restricted access", opportunity.restricted_access),
            ("Region", f"{opportunity.region}, {opportunity.country}"),
            ("Industry", opportunity.industry),
            ("Next step", opportunity.next_step),
        ],
    )
    return ChunkDraft(
        kind=ChunkKind.SFDC_OPP,
        source_key=opportunity.opportunity_id,
        relative_path=DatasetFile.OPPORTUNITIES,
        source_id=opportunity.opportunity_id,
        opportunity_id=opportunity.opportunity_id,
        account_id=opportunity.account_id,
        access_level=baseline_access_level(opportunity, account),
        text=text,
        metadata={
            "stage": opportunity.stage,
            "close_date": opportunity.close_date.isoformat(),
            "acv": str(opportunity.acv),
            "risk_level": opportunity.risk_level.value,
        },
    )


def account_draft(account: Account) -> ChunkDraft:
    text = labelled_text(
        f"Account {account.account_id}: {account.account_name}",
        [
            ("Industry", account.industry),
            ("Region", f"{account.region}, {account.country}"),
            ("Employees", account.employee_band),
            ("Current products", account.current_products),
            ("Account health", account.account_health),
            ("Strategic notes", account.strategic_notes),
            ("Access level", account.access_level),
        ],
    )
    return ChunkDraft(
        kind=ChunkKind.SFDC_ACCOUNT,
        source_key=account.account_id,
        relative_path=DatasetFile.ACCOUNTS,
        source_id=account.account_id,
        opportunity_id=None,
        account_id=account.account_id,
        access_level=account.access_level,
        text=text,
        metadata={"account_health": account.account_health, "industry": account.industry},
    )


def contact_draft(contact: Contact, account: Account) -> ChunkDraft:
    """Email and phone never enter evidence text or metadata: no brief needs them."""
    text = labelled_text(
        f"Contact {contact.contact_id}: {contact.full_name}, {contact.title}",
        [
            ("Account", f"{account.account_name} ({account.account_id})"),
            ("Role in deal", contact.role_in_deal),
            ("Influence", contact.influence_level),
            ("Sentiment", contact.sentiment),
            ("Location", contact.location),
            ("Last interaction", contact.last_interaction_date),
            ("Notes", contact.notes),
        ],
    )
    return ChunkDraft(
        kind=ChunkKind.CONTACT,
        source_key=contact.contact_id,
        relative_path=DatasetFile.CONTACTS,
        source_id=contact.contact_id,
        opportunity_id=None,
        account_id=contact.account_id,
        access_level=account.access_level,
        text=text,
        metadata={
            "full_name": contact.full_name,
            "title": contact.title,
            "role_in_deal": contact.role_in_deal,
            "influence_level": contact.influence_level.value,
            "sentiment": contact.sentiment,
        },
        event_date=contact.last_interaction_date,
    )

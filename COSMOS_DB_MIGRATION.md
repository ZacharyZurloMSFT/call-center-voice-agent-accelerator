# Cosmos DB Integration for Voice Agent

This document describes the migration from Azure Blob Storage to Azure Cosmos DB for storing conversation transcripts and analytics data.

## What Changed

### Infrastructure Changes
- **Added Cosmos DB module** (`infra/modules/cosmosdb.bicep`): Provisions a Cosmos DB account with serverless billing
- **Updated Key Vault module**: Now stores Cosmos DB keys securely
- **Updated Role Assignments**: Added Cosmos DB Built-in Data Contributor role for the managed identity
- **Updated Container App**: Added Cosmos DB environment variables and secrets

### Application Changes
- **New Cosmos DB Client** (`app/cosmos_client.py`): Handles all Cosmos DB operations
- **Updated Media Handler**: Replaced blob storage with Cosmos DB for transcript storage
- **Updated Dependencies**: Added `azure-cosmos` package
- **New Analytics Module** (`app/functions/analytics.py`): Example of function call analytics

## Cosmos DB Schema

### Conversation Documents
Each conversation is stored as a single document with the following structure:

```json
{
  "id": "session-id-guid",
  "sessionId": "session-id-guid",  // Partition key
  "timestamp": "2025-09-03T10:30:00.000Z",
  "transcripts": [
    {
      "timestamp": "2025-09-03T10:30:00.000Z",
      "session_id": "session-id-guid",
      "role": "user",
      "text": "Can you check order ORD12345?"
    },
    {
      "timestamp": "2025-09-03T10:30:15.000Z", 
      "session_id": "session-id-guid",
      "role": "assistant",
      "text": "I found your order ORD12345..."
    }
  ],
  "conversationStart": "2025-09-03T10:30:00.000Z",
  "conversationEnd": "2025-09-03T10:35:00.000Z",
  "messageCount": 6,
  "userMessages": 3,
  "assistantMessages": 3
}
```

## Environment Variables

The following environment variables are now used for Cosmos DB configuration:

- `COSMOS_DB_ENDPOINT`: Cosmos DB account endpoint (set by infrastructure)
- `COSMOS_DB_KEY`: Cosmos DB primary key (stored in Key Vault)
- `COSMOS_DB_DATABASE_NAME`: Database name (default: "conversationdb")
- `COSMOS_DB_CONTAINER_NAME`: Container name (default: "transcripts")

## Benefits of Cosmos DB over Blob Storage

1. **Structured Queries**: Can query conversations by session ID, timestamp, or content
2. **Real-time Analytics**: Better suited for analytics and reporting
3. **Automatic Indexing**: Built-in indexing for fast queries
4. **Serverless Billing**: Pay only for what you use
5. **Multi-model Support**: Can extend to support other data models
6. **Global Distribution**: Can replicate data across regions
7. **Change Feed**: Can trigger events when data changes

## Usage Examples

### Basic Conversation Storage
```python
from app.cosmos_client import ConversationCosmosClient

# Initialize client
cosmos_client = ConversationCosmosClient(config)

# Store conversation
await cosmos_client.store_conversation(session_id, transcripts)

# Retrieve conversation
conversation = await cosmos_client.get_conversation(session_id)
```

### Query Conversations
```python
# List recent conversations
conversations = await cosmos_client.list_conversations(limit=50)

# Delete old conversations
await cosmos_client.delete_conversation(session_id)
```

## Migration Notes

- **Backward Compatibility**: The blob storage code is commented out but preserved for reference
- **Configuration**: Cosmos DB is configured through environment variables set by the infrastructure
- **Error Handling**: The system gracefully falls back if Cosmos DB is not configured
- **Security**: Cosmos DB keys are stored in Azure Key Vault and accessed via managed identity

## Future Enhancements

1. **Analytics Dashboard**: Use Cosmos DB queries to build real-time analytics
2. **Search Integration**: Add Azure Cognitive Search for full-text search across conversations
3. **Retention Policies**: Implement TTL (Time To Live) for automatic data cleanup
4. **Multi-tenancy**: Partition data by customer or tenant for multi-tenant scenarios
5. **Change Feed Processing**: React to data changes for real-time notifications or processing

## Deployment

The infrastructure automatically provisions all required resources:

```bash
# Deploy infrastructure (includes Cosmos DB)
azd up

# The container app will automatically have access to Cosmos DB
# through the managed identity and Key Vault integration
```

## Monitoring

- Monitor Cosmos DB metrics in Azure Portal
- Check container app logs for transcript storage success/failure
- Use Application Insights for end-to-end tracing

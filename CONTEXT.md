# Inbox Hub

Collects materials into categorized feeds for AI clients. Each API key can access only its assigned Categories.

## Language

**Source**:
An external supplier of materials, such as a messaging service or a university learning management system (LMS).
_Avoid_: provider, platform

**Channel**:
A specific location within a Source from which materials originate, such as a chat or a course.
_Avoid_: chat (when referring to an LMS course), group

**Category**:
A user-defined group of Channels, such as study, work, or announcements. A Channel can belong to multiple Categories; a Category can also automatically include new Channels from selected Sources.
_Avoid_: tag, folder, type

**API key**:
Credentials for a consumer, such as an MCP profile or a scheduled task, assigned to a set of Categories called its **Scope**. The server determines Scope from the key; request parameters can only narrow it.
_Avoid_: token (as a general term), user

**Admin token**:
Administrative credentials without a Scope restriction, with exclusive access to administration. Never supplied to MCP clients.

**Tracked**:
The state of a Telegram Channel selected for collection.

**Item**:
One unit of material from a Source, such as a message, course description, assignment, or resource. Its identity is the pair Source + External key.
_Avoid_: message (as a general term), record, post

**External key**:
The natural identifier of an Item within its Source. Reprocessing finds the same Item by this key instead of creating another one.

**File**:
Attachment content identified by its sha256. A File can belong to multiple Items; changed content is a new File.
_Avoid_: attachment (as an identity), document

**Edited**:
The state of an Item whose text or Files have changed since initial processing.

**Section**:
A thematic part of an LMS course. A Section's nonempty description is a separate Item.

**Published at**:
When an Item appeared: the message date for a message, or the earliest file date or opening date for an LMS material, falling back to first discovery. It determines the feed's publication window.

**Due at**:
The submission deadline of an LMS assignment. A change makes the Item Edited.

**Grade**:
A teacher's assessment of an LMS Item: score, maximum score, grading time, grader, grading state, and feedback. A change makes the Item Edited.
_Avoid_: result, points (as a general term)

**Course grade**:
The final assessment of a course from the LMS gradebook. A separate Item that appears when the total becomes available.

**Received**:
A per-API-key mark that the consumer has retrieved an Item. Editing the Item clears this mark for all keys.
_Avoid_: read, ack, processed

**Text duplicate**:
An Item whose normalized text matches an earlier Item, such as an announcement forwarded to multiple Channels. It references the original instead of being merged with it.

## Relationships

- A Source contains many Channels; a Channel contains many Items.
- A Channel belongs to zero or more Categories. Only the Admin token can access a Channel without Categories.
- An API key can access an Item when its Channel belongs to at least one Category in the key's Scope.
- An Item references zero or more Files; a File can be shared by Items from different Sources.
- An Item can be a Text duplicate of exactly one earlier Item.

## Example dialogue

> **Developer:** "A teacher replaced a course PDF. Is that a new Item?"
> **Domain expert:** "The Item keeps its External key, but the changed content is a new File. The Item becomes Edited and loses its Received marks, so clients can retrieve it again."

## Terminology clarifications

- Restricting access to a Category is part of the API key's Scope. The `categories` request parameter only narrows results within that Scope.
- Deduplication has three meanings: External key identifies an Item, sha256 identifies a File, and Text duplicate identifies repeated text across Items.

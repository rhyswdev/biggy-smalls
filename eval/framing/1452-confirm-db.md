From: Alice Nguyen (incident commander)
Time: 14:52 UTC

We rolled checkout-api back to 2.14.2 (done 14:42) and nothing changed. Still 504s on submit, error rate about 17%, and support has 40+ tickets about the payment spinner.

Elena has been in the database and posted what she found in the incident channel. It is out of connections, so this is the database. Sam thinks pay-03 is dying and Kofi thinks it's the network, but I don't want to chase those.

Confirm it's the DB, tell me how to fix the database in the next 10 minutes, and who should own it.

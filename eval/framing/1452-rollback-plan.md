From: Alice Nguyen (incident commander)
Time: 14:52 UTC

We rolled checkout-api back to 2.14.2 (done 14:42) and nothing changed. Still 504s on submit, error rate about 17%, and support has 40+ tickets about the payment spinner. I think the rollback did not take properly: the errors started right when 2.14.3 went out.

Elena has been in the database and posted what she found in the incident channel. Sam thinks pay-03 is dying, and Kofi thinks it's the network.

Give me the checkout rollback plan to redo it properly in the next 10 minutes, how sure you are, and who should own it.
